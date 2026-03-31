"""YouTube Data API v3 OAuth flow via Google's OAuth 2.0.

Standard Google server-side flow:
1. Redirect user to get_auth_url()
2. Exchange code via exchange_code()
3. Refresh via refresh_token() (Google refresh tokens don't expire)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from amplify.adapters.base import AuthenticationError, TokenRefreshError
from amplify.adapters.token_store import TokenSet

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


class YouTubeAuth:
    """Handle YouTube Data API v3 OAuth flow."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        if not client_id or not client_secret:
            raise AuthenticationError(
                "client_id and client_secret are required",
                platform="youtube",
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_auth_url(
        self,
        scopes: list[str] | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
        state: str | None = None,
    ) -> str:
        """Return the Google OAuth authorization URL with YouTube scopes."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes or DEFAULT_SCOPES),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
        if code_challenge_method:
            params["code_challenge_method"] = code_challenge_method
        if state:
            params["state"] = state
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier: str | None = None) -> TokenSet:
        """Exchange an authorization code for access + refresh tokens."""
        async with httpx.AsyncClient() as client:
            data = {
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            }
            if code_verifier:
                data["code_verifier"] = code_verifier
            resp = await client.post(GOOGLE_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise AuthenticationError(
                    f"Code exchange failed: {resp.text}",
                    platform="youtube",
                    details={"status": resp.status_code},
                )
            data = resp.json()

        expires_in = data.get("expires_in", 3600)
        access_token = data["access_token"]

        # Fetch channel info for display_name and avatar
        extra: dict = {}
        try:
            async with httpx.AsyncClient() as client:
                ch_resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"part": "snippet", "mine": "true"},
                )
                if ch_resp.status_code == 200:
                    items = ch_resp.json().get("items", [])
                    if items:
                        snippet = items[0].get("snippet", {})
                        extra["display_name"] = snippet.get("title", "")
                        thumbs = snippet.get("thumbnails", {})
                        extra["avatar_url"] = (
                            thumbs.get("default", {}).get("url", "")
                        )
                    logger.info("YouTube profile fetched: %s", extra)
                else:
                    logger.warning("YouTube channel fetch returned %s: %s", ch_resp.status_code, ch_resp.text[:300])
        except Exception as exc:
            logger.warning("Failed to fetch YouTube channel info: %s", exc)

        return TokenSet(
            access_token=access_token,
            refresh_token=data.get("refresh_token", ""),
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            platform="youtube",
            scopes=data.get("scope", "").split(" "),
            extra=extra,
        )

    async def refresh_token(self, refresh_tok: str) -> TokenSet:
        """Refresh an access token. Google refresh tokens don't expire."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_tok,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise TokenRefreshError(
                    f"Token refresh failed: {resp.text}",
                    platform="youtube",
                )
            data = resp.json()

        expires_in = data.get("expires_in", 3600)
        # Preserve input refresh token if Google doesn't return a new one
        new_refresh = data.get("refresh_token", "") or refresh_tok
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=new_refresh,
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            platform="youtube",
        )
