"""Local node configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class NodeConfig(BaseSettings):
    """Settings for the Amplify local node."""

    model_config = {"env_prefix": "AMPLIFY_NODE_"}

    # Identity
    node_id: str = ""  # auto-generated if empty
    tenant_id: str = "local"

    # API connection
    api_url: str = "http://localhost:8000"

    # Intervals (seconds)
    heartbeat_interval: int = 30
    scheduler_poll_interval: int = 60
    offline_flush_interval: int = 15

    # Paths
    data_dir: Path = Path.home() / ".amplify-os"
    cache_dir: Path = Path.home() / ".amplify-os" / "cache"
    media_output_dir: Path = Path.home() / ".amplify-os" / "media"

    # Heartbeat server
    heartbeat_host: str = "0.0.0.0"
    heartbeat_port: int = 8100

    # Process supervisor
    max_restart_attempts: int = 10
    restart_backoff_base: float = 1.0
    restart_backoff_max: float = 60.0

    # Offline queue
    offline_db_path: Path = Path.home() / ".amplify-os" / "offline_queue.db"
    max_offline_queue_size: int = 1000

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    def ensure_dirs(self) -> None:
        """Create data directories if they don't exist."""
        for d in [self.data_dir, self.cache_dir, self.media_output_dir]:
            d.mkdir(parents=True, exist_ok=True)
