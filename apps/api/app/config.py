from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, loaded from environment variables and .env file."""

    deployment_mode: str = "local"
    database_url: str = "postgresql+asyncpg://localhost:5432/amplify"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 7
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Token encryption
    token_encryption_key: str = ""
    token_encryption_old_keys: list[str] = []

    # OAuth — Instagram
    instagram_client_id: str = ""
    instagram_client_secret: str = ""
    instagram_redirect_uri: str = ""

    # OAuth — YouTube / Google
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = ""

    # OAuth — TikTok
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""

    # Frontend URL for OAuth callback redirects
    frontend_url: str = "http://localhost:3000"

    @property
    def is_local(self) -> bool:
        return self.deployment_mode == "local"

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
