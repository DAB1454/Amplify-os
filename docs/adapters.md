# Platform Adapters

## Overview

Platform adapters are pluggable connectors that integrate Amplify-OS with social media and streaming platforms. Each adapter implements a common interface, allowing the campaign engine to publish content and collect metrics without platform-specific logic.

## Adapter Interface Contract

Every adapter must implement the `PlatformAdapter` protocol:

```python
class PlatformAdapter(Protocol):
    platform: str  # e.g., "instagram", "tiktok", "spotify"

    async def authenticate(self, credentials: dict) -> AuthResult:
        """Establish or refresh platform authentication."""
        ...

    async def publish(self, post: Post) -> PublishResult:
        """Publish content to the platform."""
        ...

    async def get_metrics(self, post_id: str) -> PlatformMetrics:
        """Retrieve engagement metrics for a published post."""
        ...

    async def validate_connection(self) -> bool:
        """Check if the current credentials are still valid."""
        ...

    async def revoke(self) -> None:
        """Revoke access and clean up stored credentials."""
        ...
```

## How to Add a New Adapter

1. **Create the module** at `packages/adapters/<platform>.py`.
2. **Implement** the `PlatformAdapter` protocol.
3. **Register** the adapter in `packages/adapters/registry.py`:
   ```python
   ADAPTERS["new_platform"] = NewPlatformAdapter
   ```
4. **Add auth config** -- set up OAuth app credentials in the platform's developer portal and add the config to environment variables.
5. **Write tests** in `tests/adapters/test_<platform>.py`.
6. **Add UI entry** -- update the web app's connection settings page to include the new platform.

## Authentication Patterns

### OAuth 2.0 (Most Platforms)
Used by: Instagram, TikTok, YouTube, Spotify, Twitter/X

1. User clicks "Connect" in the dashboard.
2. API redirects to platform's OAuth consent screen.
3. Platform redirects back with an authorization code.
4. API exchanges code for access + refresh tokens.
5. Tokens are encrypted and stored per-tenant.
6. Worker uses refresh tokens to maintain access.

### API Key
Used by: Some analytics and distribution platforms

1. User enters their API key in settings.
2. Key is encrypted and stored per-tenant.
3. Adapter includes key in request headers.

### Session / Cookie (Legacy)
Used by: Platforms without public APIs (rare, use cautiously)

1. Headless browser establishes session.
2. Session cookies are stored encrypted.
3. Adapter replays cookies for requests.

## Rate Limit Handling

Each adapter implements rate-limit awareness:

- **Headers**: Read `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers.
- **Backoff**: Exponential backoff with jitter on 429 responses.
- **Queuing**: If near the limit, defer the action to the next window.
- **Redis tracking**: Per-adapter, per-tenant rate-limit counters in Redis.

```python
class RateLimiter:
    async def acquire(self, platform: str, tenant_id: str) -> bool:
        """Return True if the request can proceed, False if rate-limited."""
        ...

    async def record(self, platform: str, tenant_id: str, headers: dict) -> None:
        """Update rate-limit state from response headers."""
        ...
```
