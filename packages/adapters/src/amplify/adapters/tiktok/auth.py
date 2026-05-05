"""TikTok Login Kit OAuth flow.

Implements the server-side OAuth 2.0 flow for TikTok:
1. Redirect user to get_auth_url()
2. Exchange code via exchange_code()
3. Refresh via refresh_token()

Uses TikTok API v2 endpoints.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from amplify.adapters.base import AuthenticationError, TokenRefreshError
from amplify.adapters.token_store import TokenSet

logger = logging.getLogger(__name__)

TT_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def _variant_label() -> str:
    """Return TIKTOK_OAUTH_VARIANT for log identification (sandbox/production).

    Doesn't change behavior — purely a label so it's obvious from logs which
    TikTok app's credentials are currently loaded. Useful when swapping to
    sandbox keys for a Direct Post audit demo.
    """
    return os.environ.get("TIKTOK_OAUTH_VARIANT", "unset")


class TikTokAuth:
    """Handle TikTok Login Kit OAuth flow."""

    def __init__(
        self,
        client_key: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        if not client_key or not client_secret:
            raise AuthenticationError(
                "client_key and client_secret are required",
                platform="tiktok",
            )
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_auth_url(
        self,
        scopes: list[str] | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
        state: str | None = None,
    ) -> str:
        """Return the TikTok OAuth authorization URL.

        Default scope set requests all four scopes that the AmplifyMe app
        is approved for: user.info.basic (Login Kit), video.upload (Inbox),
        video.publish (Direct Post), video.list (analytics dashboard).
        Drop video.list explicitly only when targeting an app that hasn't
        been approved for it.
        """
        default_scopes = [
            "user.info.basic",
            "video.upload",
            "video.publish",
            "video.list",
        ]
        params = {
            "client_key": self.client_key,
            "redirect_uri": self.redirect_uri,
            "scope": ",".join(scopes or default_scopes),
            "response_type": "code",
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
        if code_challenge_method:
            params["code_challenge_method"] = code_challenge_method
        if state:
            params["state"] = state
        logger.info(
            "TikTok OAuth url built (variant=%s scope=%s)",
            _variant_label(), params["scope"],
        )
        return f"{TT_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier: str | None = None) -> TokenSet:
        """Exchange an authorization code for access tokens."""
        async with httpx.AsyncClient() as client:
            data = {
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            }
            if code_verifier:
                data["code_verifier"] = code_verifier
            resp = await client.post(
                TT_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                raise AuthenticationError(
                    f"Code exchange failed: {resp.text}",
                    platform="tiktok",
                )
            data = resp.json()

        if "error" in data:
            raise AuthenticationError(
                f"TikTok auth error: {data.get('error_description', data['error'])}",
                platform="tiktok",
            )

        expires_in = data.get("expires_in", 86400)
        access_token = data["access_token"]
        open_id = data.get("open_id", "")
        granted_scope = data.get("scope", "")
        logger.info(
            "TikTok OAuth code exchanged (variant=%s open_id=%s scope=%s)",
            _variant_label(), open_id, granted_scope,
        )

        # Fetch user profile for display_name and avatar_url
        extra: dict = {}
        try:
            async with httpx.AsyncClient() as client:
                profile_resp = await client.get(
                    "https://open.tiktokapis.com/v2/user/info/",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"fields": "open_id,display_name,avatar_url"},
                )
                if profile_resp.status_code == 200:
                    user = profile_resp.json().get("data", {}).get("user", {})
                    extra["display_name"] = user.get("display_name", "")
                    extra["avatar_url"] = user.get("avatar_url", "")
                    logger.info("TikTok profile fetched: %s", extra)
                else:
                    logger.warning("TikTok profile fetch returned %s: %s", profile_resp.status_code, profile_resp.text[:300])
        except Exception as exc:
            logger.warning("Failed to fetch TikTok user profile: %s", exc)

        return TokenSet(
            access_token=access_token,
            refresh_token=data.get("refresh_token", ""),
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            platform="tiktok",
            account_id=open_id,
            scopes=data.get("scope", "").split(","),
            extra=extra,
        )

    async def refresh_token(self, refresh_tok: str) -> TokenSet:
        """Refresh an access token using the refresh token."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TT_TOKEN_URL,
                data={
                    "client_key": self.client_key,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_tok,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                raise TokenRefreshError(
                    f"Token refresh failed: {resp.text}",
                    platform="tiktok",
                )
            data = resp.json()

        if "error" in data:
            raise TokenRefreshError(
                f"Refresh error: {data.get('error_description', data['error'])}",
                platform="tiktok",
            )

        expires_in = data.get("expires_in", 86400)
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", refresh_tok),
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            platform="tiktok",
            account_id=data.get("open_id", ""),
        )
