"""CLI entry point for the Amplify OS local node runtime.

The local node is a self-contained daemon that runs on an artist's machine.
It manages: a job worker, a periodic scheduler, a media renderer, browser
automation tasks, and a health endpoint — all under a process supervisor
with auto-restart.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid

import click
import httpx
import uvicorn

from node.app.config import NodeConfig
from node.app.heartbeat import HeartbeatClient, create_heartbeat_app
from node.app.local_scheduler import LocalScheduler
from node.app.local_worker import LocalWorker
from node.app.offline_queue import OfflineQueue
from node.app.secrets_store import SecretsStore
from node.app.supervisor import Supervisor
from node.app.ffmpeg_runner import FFmpegRunner
from node.app.playwright_runner import PlaywrightRunner

logger = logging.getLogger(__name__)


def _detect_capabilities() -> list[str]:
    """Detect which local capabilities are available."""
    caps = []
    if FFmpegRunner().check_installed():
        caps.append("ffmpeg")
    if PlaywrightRunner().check_installed():
        caps.append("playwright")
    return caps


async def _run_node(config: NodeConfig) -> None:
    """Main async entry: wire up all components and run under supervision."""
    config.ensure_dirs()

    # Generate a stable node ID (persisted in secrets store)
    secrets = SecretsStore(path=config.data_dir / "secrets.enc", key_path=config.data_dir / ".secrets.key")
    node_id = secrets.get("node_id")
    if not node_id:
        node_id = uuid.uuid4().hex[:12]
        secrets.set("node_id", node_id)
    config.node_id = node_id

    capabilities = _detect_capabilities()
    logger.info("Node %s starting (tenant=%s, caps=%s)", node_id, config.tenant_id, capabilities)

    # Components
    offline_queue = OfflineQueue(db_path=config.offline_db_path, max_size=config.max_offline_queue_size)
    heartbeat_client = HeartbeatClient()

    worker = LocalWorker(config=config, offline_queue=offline_queue)
    scheduler = LocalScheduler(
        config=config,
        heartbeat_client=heartbeat_client,
        offline_queue=offline_queue,
        capabilities=capabilities,
    )

    # Supervisor
    supervisor = Supervisor(
        max_attempts=config.max_restart_attempts,
        backoff_base=config.restart_backoff_base,
        backoff_max=config.restart_backoff_max,
    )
    supervisor.register("worker", worker.run)
    supervisor.register("scheduler", scheduler.run)

    # Heartbeat HTTP server
    heartbeat_app = create_heartbeat_app(
        node_id=node_id,
        supervisor_status_fn=supervisor.status,
        offline_queue_size_fn=offline_queue.size,
    )

    http_config = uvicorn.Config(
        heartbeat_app,
        host=config.heartbeat_host,
        port=config.heartbeat_port,
        log_level="warning",
    )
    http_server = uvicorn.Server(http_config)

    # Shutdown handler
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    # Start everything
    await supervisor.start()
    http_task = asyncio.create_task(http_server.serve())

    click.echo(f"Amplify node {node_id} running")
    click.echo(f"  Health: http://{config.heartbeat_host}:{config.heartbeat_port}/health")
    click.echo(f"  API:    {config.api_url}")
    click.echo(f"  Caps:   {capabilities or ['none']}")
    click.echo(f"  Data:   {config.data_dir}")

    # Wait for shutdown
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    # Graceful shutdown
    logger.info("Shutting down...")
    await supervisor.stop()
    http_server.should_exit = True
    await http_task
    offline_queue.close()
    logger.info("Node %s stopped", node_id)


# ── CLI ──────────────────────────────────────────────────────────────


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool) -> None:
    """Amplify OS Local Node — run the full stack on your machine."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@cli.command()
@click.option("--api-url", envvar="AMPLIFY_NODE_API_URL", default="http://localhost:8000")
@click.option("--tenant-id", envvar="AMPLIFY_NODE_TENANT_ID", default="local")
@click.option("--port", envvar="AMPLIFY_NODE_HEARTBEAT_PORT", default=8100, type=int)
def start(api_url: str, tenant_id: str, port: int) -> None:
    """Start the local node daemon."""
    config = NodeConfig(api_url=api_url, tenant_id=tenant_id, heartbeat_port=port)
    try:
        asyncio.run(_run_node(config))
    except KeyboardInterrupt:
        click.echo("\nShutdown complete.")


@cli.command()
@click.option("--api-url", envvar="AMPLIFY_NODE_API_URL", default="http://localhost:8000")
def status(api_url: str) -> None:
    """Check API connectivity and local node health."""
    # Check API
    try:
        resp = httpx.get(f"{api_url}/health", timeout=5)
        click.echo(f"API:  {api_url} → HTTP {resp.status_code}")
    except httpx.ConnectError:
        click.echo(f"API:  {api_url} → UNREACHABLE")
    except Exception as exc:
        click.echo(f"API:  {api_url} → ERROR ({exc})")

    # Check local health endpoint
    try:
        resp = httpx.get("http://localhost:8100/health", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            click.echo(f"Node: {data['node_id']} (uptime {data['uptime_seconds']:.0f}s)")
            click.echo(f"  Offline queue: {data['offline_queue_size']} jobs")
            for name, proc in data.get("processes", {}).items():
                click.echo(f"  {name}: {proc['state']} (restarts: {proc['restart_count']})")
        else:
            click.echo(f"Node: HTTP {resp.status_code}")
    except httpx.ConnectError:
        click.echo("Node: NOT RUNNING")

    # Check capabilities
    caps = _detect_capabilities()
    click.echo(f"Caps: {caps or ['none detected']}")


@cli.command()
def capabilities() -> None:
    """Detect and display local capabilities."""
    ffmpeg = FFmpegRunner()
    pw = PlaywrightRunner()

    click.echo("Capability check:")
    click.echo(f"  ffmpeg:     {'✓ installed' if ffmpeg.check_installed() else '✗ not found'}")
    click.echo(f"  playwright: {'✓ installed' if pw.check_installed() else '✗ not found'}")


@cli.command()
@click.argument("key")
@click.argument("value", required=False)
@click.option("--delete", is_flag=True, help="Delete the secret.")
def secret(key: str, value: str | None, delete: bool) -> None:
    """Get, set, or delete a secret in the encrypted store."""
    store = SecretsStore()
    if delete:
        store.delete(key)
        click.echo(f"Deleted: {key}")
    elif value is not None:
        store.set(key, value)
        click.echo(f"Stored: {key}")
    else:
        val = store.get(key)
        if val:
            click.echo(val)
        else:
            click.echo(f"No secret found for: {key}")


@cli.command("list-secrets")
def list_secrets() -> None:
    """List all secret names (not values)."""
    store = SecretsStore()
    keys = store.list_keys()
    if keys:
        for k in keys:
            click.echo(f"  {k}")
    else:
        click.echo("No secrets stored.")


@cli.command("queue-status")
def queue_status() -> None:
    """Show offline queue status."""
    config = NodeConfig()
    queue = OfflineQueue(db_path=config.offline_db_path)
    size = queue.size()
    click.echo(f"Offline queue: {size} job(s)")
    if size > 0:
        jobs = queue.peek(limit=10)
        for job in jobs:
            click.echo(f"  {job.job_id[:8]}  {job.job_type:20s}  attempts={job.attempts}  {job.created_at}")
    queue.close()


if __name__ == "__main__":
    cli()
