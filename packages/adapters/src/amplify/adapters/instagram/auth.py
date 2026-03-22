"""Instagram Graph API OAuth flow.

Implements the server-side flow for Instagram Basic Display / Graph API:
1. Redirect user to get_auth_url()
2. Exchange the code via exchange_code()
3. Convert short-lived token to long-lived via exchange_long_lived()
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

IG_AUTH_URL = "https://www.facebook.com/v19.0/dialog/oauth"
IG_TOKEN_URL = "https://graph.facebook.com/v19.0/oauth/access_token"
IG_GRAPH_URL = "https://graph.facebook.com/v19.0"


class InstagramAuth:
    """Handle Instagram Graph API OAuth flow."""

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
        """Return the Facebook OAuth authorization URL for Instagram Graph API."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": ",".join(scopes or [
                "public_profile",
                "email",
            ]),
            "response_type": "code",
        }
        if state:
            params["state"] = state
        return f"{IG_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier: str | None = None) -> TokenSet:
        """Exchange an authorization code for an access token via Facebook Graph API."""
        async with httpx.AsyncClient() as client:
            params = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            }
            if code_verifier:
                params["code_verifier"] = code_verifier
            resp = await client.get(IG_TOKEN_URL, params=params)
            if resp.status_code != 200:
                raise AuthenticationError(
                    f"Code exchange failed: {resp.text}",
                    platform="instagram",
                    details={"status": resp.status_code},
                )
            token_data = resp.json()

        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 5184000)

        # Resolve the Instagram Business/Creator account ID from Facebook pages
        account_id = await self._resolve_ig_account_id(access_token)

        return TokenSet(
            access_token=access_token,
            platform="instagram",
            account_id=account_id,
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
        )

    async def _resolve_ig_account_id(self, access_token: str) -> str:
        """Find the Instagram Business/Creator account linked to the user's Facebook pages."""
        async with httpx.AsyncClient() as client:
            # Get user's Facebook pages
            resp = await client.get(
                f"{IG_GRAPH_URL}/me/accounts",
                params={"access_token": access_token},
            )
            if resp.status_code != 200:
                logger.warning("Failed to fetch Facebook pages: %s", resp.text)
                return ""

            pages = resp.json().get("data", [])
            if not pages:
                logger.warning("No Facebook pages found — Instagram Business account requires a linked page")
                return ""

            # Check each page for a connected Instagram account
            for page in pages:
                page_id = page["id"]
                page_token = page.get("access_token", access_token)
                ig_resp = await client.get(
                    f"{IG_GRAPH_URL}/{page_id}",
                    params={
                        "fields": "instagram_business_account",
                        "access_token": page_token,
                    },
                )
                if ig_resp.status_code == 200:
                    ig_data = ig_resp.json()
                    ig_account = ig_data.get("instagram_business_account", {})
                    if ig_account.get("id"):
                        logger.info("Found Instagram Business account: %s", ig_account["id"])
                        return ig_account["id"]

            logger.warning("No Instagram Business account found on any Facebook page")
            return ""

    async def exchange_long_lived(self, short_lived_token: str) -> TokenSet:
        """Convert a short-lived token to a long-lived token (~60 days)."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{IG_GRAPH_URL}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "fb_exchange_token": short_lived_token,
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
