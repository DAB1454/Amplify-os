"""X (Twitter) OAuth 2.0 with PKCE.

Twitter API v2 uses standard OAuth 2.0 Authorization Code Flow with PKCE.
Refresh tokens are supported and long-lived.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from amplify.adapters.base import AuthenticationError, TokenRefreshError
from amplify.adapters.token_store import TokenSet

logger = logging.getLogger(__name__)

TWITTER_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TWITTER_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"

DEFAULT_SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "offline.access",       # required for refresh tokens
    "media.upload",         # required for image/video uploads (v1.1 media)
]


class TwitterAuth:
    """Handle X (Twitter) OAuth 2.0 PKCE flow."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        if not client_id:
            raise AuthenticationError(
                "client_id is required",
                platform="twitter",
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
        """Return the Twitter OAuth 2.0 authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes or DEFAULT_SCOPES),
            "response_type": "code",
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
        if code_challenge_method:
            params["code_challenge_method"] = code_challenge_method
        if state:
            params["state"] = state
        return f"{TWITTER_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier: str | None = None) -> TokenSet:
        """Exchange an authorization code for access + refresh tokens."""
        data = {
            "code": code,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        # Twitter requires Basic auth with client_id:client_secret for
        # confidential clients. Public clients send client_id in body only.
        auth = None
        if self.client_secret:
            auth = (self.client_id, self.client_secret)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TWITTER_TOKEN_URL,
                data=data,
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                raise AuthenticationError(
                    f"Code exchange failed: {resp.text}",
                    platform="twitter",
                    details={"status": resp.status_code},
                )
            body = resp.json()

        expires_in = body.get("expires_in", 7200)
        access_token = body["access_token"]

        # Fetch user profile for display_name and account_id
        extra: dict = {}
        account_id = ""
        try:
            async with httpx.AsyncClient() as client:
                me_resp = await client.get(
                    "https://api.twitter.com/2/users/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"user.fields": "name,username,profile_image_url"},
                )
                if me_resp.status_code == 200:
                    user_data = me_resp.json().get("data", {})
                    account_id = user_data.get("id", "")
                    extra["display_name"] = user_data.get("name", "")
                    extra["username"] = user_data.get("username", "")
                    extra["avatar_url"] = user_data.get("profile_image_url", "")
                    logger.info("Twitter profile fetched: @%s", extra.get("username"))
                else:
                    logger.warning("Twitter /users/me returned %s: %s", me_resp.status_code, me_resp.text[:300])
        except Exception as exc:
            logger.warning("Failed to fetch Twitter profile: %s", exc)

        return TokenSet(
            access_token=access_token,
            refresh_token=body.get("refresh_token", ""),
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            platform="twitter",
            scopes=body.get("scope", "").split(" "),
            account_id=account_id,
            extra=extra,
        )

    async def refresh_token(self, refresh_tok: str) -> TokenSet:
        """Refresh an access token."""
        auth = None
        if self.client_secret:
            auth = (self.client_id, self.client_secret)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TWITTER_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "refresh_token": refresh_tok,
                    "grant_type": "refresh_token",
                },
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                raise TokenRefreshError(
                    f"Token refresh failed: {resp.text}",
                    platform="twitter",
                )
            body = resp.json()

        expires_in = body.get("expires_in", 7200)
        # Twitter returns a new refresh token on every refresh
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", "") or refresh_tok,
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            platform="twitter",
        )
