"""Local scheduler that runs periodic tasks on the artist's machine."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from node.app.config import NodeConfig
from node.app.heartbeat import HeartbeatClient
from node.app.offline_queue import OfflineQueue

logger = logging.getLogger(__name__)


class LocalScheduler:
    """Runs periodic background tasks:

    - Heartbeat registration with the API
    - Offline queue flush
    - Scheduled post scanning
    - Metric sync
    - Media cache cleanup
    """

    def __init__(
        self,
        config: NodeConfig,
        heartbeat_client: HeartbeatClient,
        offline_queue: OfflineQueue,
        capabilities: list[str],
    ) -> None:
        self._config = config
        self._heartbeat = heartbeat_client
        self._offline_queue = offline_queue
        self._capabilities = capabilities
        self._running = False

    async def run(self) -> None:
        """Start all scheduled loops concurrently."""
        self._running = True
        logger.info("Local scheduler started")

        await asyncio.gather(
            self._heartbeat_loop(),
            self._scan_scheduled_posts_loop(),
            self._sync_metrics_loop(),
            return_exceptions=True,
        )

    async def stop(self) -> None:
        self._running = False

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats to the API at a fixed interval."""
        while self._running:
            ok = await self._heartbeat.send(
                self._config.api_url,
                self._config.node_id,
                self._capabilities,
            )
            if ok:
                logger.debug("Heartbeat OK")
            else:
                logger.debug(
                    "Heartbeat failed (%d consecutive)",
                    self._heartbeat.consecutive_failures,
                )
            await asyncio.sleep(self._config.heartbeat_interval)

    async def _scan_scheduled_posts_loop(self) -> None:
        """Check for posts that are due for publishing."""
        while self._running:
            await asyncio.sleep(self._config.scheduler_poll_interval)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{self._config.api_url}/api/v1/posts",
                        params={
                            "status": "scheduled",
                            "due_before": datetime.now(timezone.utc).isoformat(),
                        },
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        posts = resp.json()
                        for post in posts:
                            logger.info("Post %s is due for publishing", post.get("id"))
                            self._offline_queue.enqueue(
                                "publish_post", {"post_id": post["id"]}
                            )
            except (httpx.ConnectError, httpx.TimeoutException):
                logger.debug("API unreachable for scheduled post scan")
            except Exception:
                logger.exception("Error scanning scheduled posts")

    async def _sync_metrics_loop(self) -> None:
        """Periodically trigger metric syncs for connected channels."""
        while self._running:
            # Run every 15 minutes
            await asyncio.sleep(900)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{self._config.api_url}/api/v1/channels",
                        params={"integration_mode": "automatic"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        channels = resp.json()
                        for ch in channels:
                            self._offline_queue.enqueue(
                                "sync_metrics", {"channel_id": ch["id"]}
                            )
                        if channels:
                            logger.info("Queued metric sync for %d channels", len(channels))
            except (httpx.ConnectError, httpx.TimeoutException):
                logger.debug("API unreachable for metric sync")
            except Exception:
                logger.exception("Error queuing metric syncs")
