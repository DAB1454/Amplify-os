"""Worker configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost:5432/amplify"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"

    # Queue configuration
    max_retries: int = 3
    backoff_base: int = 2  # seconds — exponential backoff base
    backoff_cap: int = 300  # 5 minutes — max backoff delay
    job_timeout: int = 300  # 5 minutes — stuck job detection threshold
    dedup_ttl: int = 3600  # 1 hour — idempotency key TTL
    stuck_job_check_interval: int = 60  # how often to scan for stuck jobs

    def model_post_init(self, __context: object) -> None:
        # Render provides postgres:// but SQLAlchemy async needs postgresql+asyncpg://
        if self.database_url.startswith("postgres://"):
            object.__setattr__(
                self,
                "database_url",
                self.database_url.replace("postgres://", "postgresql+asyncpg://", 1),
            )
        elif self.database_url.startswith("postgresql://"):
            object.__setattr__(
                self,
                "database_url",
                self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1),
            )

    model_config = {"env_prefix": "AMPLIFY_"}
