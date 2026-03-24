"""Instagram Business Login OAuth flow.

Uses the new Instagram Business Login (not Facebook Login):
1. Redirect user to get_auth_url() → instagram.com/oauth/authorize
2. Exchange the code via exchange_code()
3. Exchange for long-lived token via exchange_long_lived() (~60 days)
4. Refresh before expiry via refresh_token()

Never hardcode secrets — pass them via environment or config.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from amplify.adapters.base import AuthenticationError, TokenRefreshError
from amplify.adapters.token_store import TokenSet

logger = logging.getLogger(__name__)

# Instagram Business Login endpoints
IG_AUTH_URL = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
IG_GRAPH_URL = "https://graph.instagram.com"


class InstagramAuth:
    """Handle Instagram Business Login OAuth flow."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        if not client_id or not client_secret:
            raise AuthenticationError(
                "client_id and client_secret are required",
                platform="instagram",
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_auth_url(
        self,
        scopes: list[str] | None = None,
        state: str | None = None,
    ) -> str:
        """Return the Instagram Business Login authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": ",".join(scopes or [
                "instagram_business_basic",
                "instagram_business_content_publish",
                "instagram_business_manage_comments",
                "instagram_business_manage_messages",
            ]),
            "response_type": "code",
            "enable_fb_login": "0",
            "force_authentication": "1",
        }
        if state:
            params["state"] = state
        return f"{IG_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier: str | None = None) -> TokenSet:
        """Exchange an authorization code for a short-lived access token."""
        async with httpx.AsyncClient() as client:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "code": code,
            }
            resp = await client.post(IG_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise AuthenticationError(
                    f"Code exchange failed: {resp.text}",
                    platform="instagram",
                    details={"status": resp.status_code},
                )
            token_data = resp.json()

        access_token = token_data["access_token"]
        user_id = str(token_data.get("user_id", ""))

        return TokenSet(
            access_token=access_token,
            platform="instagram",
            account_id=user_id,
            # Short-lived tokens expire in ~1 hour
            expires_at=datetime.utcnow() + timedelta(hours=1),
            # Instagram doesn't return scopes in token response —
            # they were granted when the user authorized
            scopes=[
                "instagram_business_basic",
                "instagram_business_content_publish",
                "instagram_business_manage_comments",
                "instagram_business_manage_messages",
            ],
        )

    async def exchange_long_lived(self, short_lived_token: str) -> TokenSet:
        """Convert a short-lived token to a long-lived token (~60 days)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{IG_GRAPH_URL}/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": self.client_secret,
                    "access_token": short_lived_token,
                },
            )
            if resp.status_code != 200:
                raise AuthenticationError(
                    f"Long-lived token exchange failed: {resp.text}",
                    platform="instagram",
                )
            data = resp.json()

        expires_in = data.get("expires_in", 5184000)  # default 60 days
        return TokenSet(
            access_token=data["access_token"],
            platform="instagram",
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
        )

    async def refresh_token(self, long_lived_token: str) -> TokenSet:
        """Refresh a long-lived access token (valid tokens only, not expired)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{IG_GRAPH_URL}/refresh_access_token",
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": long_lived_token,
                },
            )
            if resp.status_code != 200:
                raise TokenRefreshError(
                    f"Token refresh failed: {resp.text}",
                    platform="instagram",
                )
            data = resp.json()

        expires_in = data.get("expires_in", 5184000)
        return TokenSet(
            access_token=data["access_token"],
            platform="instagram",
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
        )
