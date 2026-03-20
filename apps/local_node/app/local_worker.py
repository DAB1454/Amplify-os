"""Local worker that pulls jobs from the API and executes them locally.

Handles media rendering, browser automation, and scheduled tasks.
Falls back to the offline queue when the API is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from node.app.config import NodeConfig
from node.app.filesystem_cache import FilesystemCache
from node.app.ffmpeg_runner import FFmpegRunner
from node.app.offline_queue import OfflineQueue
from node.app.playwright_runner import PlaywrightRunner

logger = logging.getLogger(__name__)


class LocalWorker:
    """Pulls and executes jobs from the API and offline queue."""

    def __init__(
        self,
        config: NodeConfig,
        offline_queue: OfflineQueue,
    ) -> None:
        self._config = config
        self._offline_queue = offline_queue
        self._cache = FilesystemCache(config.cache_dir)
        self._ffmpeg = FFmpegRunner()
        self._playwright = PlaywrightRunner()
        self._running = False

    async def run(self) -> None:
        """Main worker loop: poll API for jobs, process them."""
        self._running = True
        logger.info("Local worker started (tenant=%s)", self._config.tenant_id)

        while self._running:
            # 1) Flush offline queue first
            await self._flush_offline_queue()

            # 2) Pull a job from the API
            job = await self._pull_job()
            if job:
                await self._execute_job(job)
            else:
                await asyncio.sleep(self._config.scheduler_poll_interval)

    async def stop(self) -> None:
        self._running = False

    async def _pull_job(self) -> dict | None:
        """Pull the next available job from the API."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._config.api_url}/api/v1/nodes/jobs",
                    params={"node_id": self._config.node_id, "limit": 1},
                    timeout=10,
                )
                if resp.status_code == 200:
                    jobs = resp.json()
                    return jobs[0] if jobs else None
                return None
        except (httpx.ConnectError, httpx.TimeoutException):
            logger.debug("API unreachable for job pull")
            return None
        except Exception:
            logger.exception("Error pulling job from API")
            return None

    async def _execute_job(self, job: dict) -> None:
        """Dispatch a job to the appropriate handler."""
        job_type = job.get("job_type", "unknown")
        job_id = job.get("job_id", "unknown")
        payload = job.get("payload", {})

        logger.info("Executing job %s (type=%s)", job_id, job_type)

        try:
            handler = self._get_handler(job_type)
            if handler:
                await handler(payload)
                await self._ack_job(job_id)
            else:
                logger.warning("No handler for job type: %s", job_type)
        except Exception as exc:
            logger.error("Job %s failed: %s", job_id, exc)
            # Queue for retry via offline queue
            self._offline_queue.enqueue(job_type, payload)
            await self._nack_job(job_id, str(exc))

    def _get_handler(self, job_type: str):
        """Return the handler coroutine for a job type."""
        handlers = {
            "render_media": self._handle_render_media,
            "browser_task": self._handle_browser_task,
            "publish_post": self._handle_publish_post,
            "sync_metrics": self._handle_sync_metrics,
            "screenshot": self._handle_screenshot,
        }
        return handlers.get(job_type)

    async def _handle_render_media(self, payload: dict) -> None:
        """Render media using local ffmpeg."""
        input_path = Path(payload["input_path"])
        output_path = Path(payload.get("output_path", self._config.media_output_dir / input_path.name))
        filters = payload.get("filters", [])

        if not self._ffmpeg.check_installed():
            raise RuntimeError("ffmpeg is not installed")

        self._ffmpeg.render(input_path, output_path, filters)
        logger.info("Rendered media: %s → %s", input_path, output_path)

    async def _handle_browser_task(self, payload: dict) -> None:
        """Execute a browser automation task via Playwright."""
        task_type = payload.get("task_type", "login")

        if not self._playwright.check_installed():
            raise RuntimeError("Playwright is not installed")

        if task_type == "login":
            result = await self._playwright.login_flow(
                payload["platform"],
                payload.get("credentials", {}),
            )
            logger.info("Browser login result: %s", result)
        elif task_type == "screenshot":
            data = await self._playwright.screenshot(payload["url"])
            if data:
                cache_key = f"screenshot:{payload['url']}"
                self._cache.put(cache_key, data)
        else:
            logger.warning("Unknown browser task type: %s", task_type)

    async def _handle_publish_post(self, payload: dict) -> None:
        """Publish a post (for assisted platforms, this creates the task)."""
        logger.info("Publish post: %s", payload.get("post_id", "unknown"))
        # Forward to API
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self._config.api_url}/api/v1/posts/{payload['post_id']}/publish",
                timeout=30,
            )

    async def _handle_sync_metrics(self, payload: dict) -> None:
        """Sync metrics for a channel."""
        logger.info("Sync metrics for channel: %s", payload.get("channel_id", "unknown"))
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self._config.api_url}/api/v1/analytics/sync",
                json=payload,
                timeout=30,
            )

    async def _handle_screenshot(self, payload: dict) -> None:
        """Take a screenshot of a URL."""
        url = payload["url"]
        if not self._playwright.check_installed():
            raise RuntimeError("Playwright is not installed")
        data = await self._playwright.screenshot(url)
        if data:
            self._cache.put(f"screenshot:{url}", data)

    async def _ack_job(self, job_id: str) -> None:
        """Acknowledge successful job completion to the API."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self._config.api_url}/api/v1/nodes/jobs/{job_id}/ack",
                    timeout=5,
                )
        except Exception:
            logger.debug("Failed to ack job %s (API unreachable)", job_id)

    async def _nack_job(self, job_id: str, error: str) -> None:
        """Report job failure to the API."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self._config.api_url}/api/v1/nodes/jobs/{job_id}/nack",
                    json={"error": error},
                    timeout=5,
                )
        except Exception:
            logger.debug("Failed to nack job %s (API unreachable)", job_id)

    async def _flush_offline_queue(self) -> None:
        """Try to re-deliver any offline-queued jobs."""
        jobs = self._offline_queue.peek(limit=5)
        if not jobs:
            return

        for job in jobs:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{self._config.api_url}/api/v1/nodes/jobs/submit",
                        json={
                            "job_type": job.job_type,
                            "payload": job.payload,
                            "node_id": self._config.node_id,
                        },
                        timeout=10,
                    )
                    if resp.status_code in (200, 201, 202):
                        self._offline_queue.ack(job.job_id)
                        logger.info("Flushed offline job %s", job.job_id)
                    else:
                        self._offline_queue.nack(job.job_id, f"HTTP {resp.status_code}")
            except (httpx.ConnectError, httpx.TimeoutException):
                logger.debug("API still unreachable — keeping %d jobs offline", len(jobs))
                return
            except Exception as exc:
                self._offline_queue.nack(job.job_id, str(exc))
