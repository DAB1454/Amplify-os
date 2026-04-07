"""Backfill published posts from platform APIs.

Runs periodically to import any published posts from connected platforms
that aren't already in the database. Uses the same import logic as the
manual import-posts endpoint but runs across all active channels.

Safe to run repeatedly — skips posts that already exist (by platform_post_id).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def backfill_posts(payload: dict | None = None) -> dict:
    """Import published posts from all active channels."""
    from amplify.db.session import get_async_session
    from amplify.db.models.channel import ChannelConnectionModel
    from amplify.db.models.post import PostModel
    from amplify.adapters.crypto import TokenEncryptor
    from amplify.adapters.token_store import TokenStore
    from app.config import Settings

    payload = payload or {}
    settings = Settings()

    total_imported = 0
    total_skipped = 0
    total_errors = 0

    async with get_async_session(settings.database_url) as db:
        # Find all active channels with tokens
        stmt = select(ChannelConnectionModel).where(
            ChannelConnectionModel.is_active == True,
            ChannelConnectionModel.access_token.isnot(None),
        )
        if payload.get("tenant_id"):
            stmt = stmt.where(
                ChannelConnectionModel.tenant_id == uuid.UUID(payload["tenant_id"])
            )
        if payload.get("platform"):
            stmt = stmt.where(ChannelConnectionModel.platform == payload["platform"])

        result = await db.execute(stmt)
        channels = list(result.scalars().all())

        if not channels:
            logger.info("No active channels to backfill")
            return {"status": "ok", "imported": 0, "skipped": 0, "errors": 0}

        logger.info("Backfilling posts for %d channels", len(channels))

        encryptor = TokenEncryptor(
            primary_key=settings.token_encryption_key,
            old_keys=settings.token_encryption_old_keys,
            deployment_mode=settings.deployment_mode,
        )

        for channel in channels:
            try:
                tokens = TokenStore.from_channel_connection(channel, encryptor)
                if not tokens.access_token:
                    continue

                platform = channel.platform
                imported = 0
                skipped = 0
                errors: list[str] = []

                if platform == "instagram":
                    imported, skipped, errors = await _import_instagram(
                        db, tokens.access_token,
                        channel.platform_account_id or "",
                        channel, channel.tenant_id,
                    )
                elif platform == "youtube":
                    imported, skipped, errors = await _import_youtube(
                        db, tokens.access_token,
                        channel, channel.tenant_id,
                    )
                # TikTok: skip until video.list scope is approved

                total_imported += imported
                total_skipped += skipped
                total_errors += len(errors)

                if imported:
                    logger.info(
                        "Backfilled %d posts from %s channel %s (skipped %d)",
                        imported, platform, channel.id, skipped,
                    )
                if errors:
                    for err in errors[:3]:
                        logger.warning("Backfill error for channel %s: %s", channel.id, err)

            except Exception as exc:
                total_errors += 1
                logger.error("Backfill failed for channel %s: %s", channel.id, exc)

    logger.info(
        "Backfill complete: %d imported, %d skipped, %d errors",
        total_imported, total_skipped, total_errors,
    )
    return {
        "status": "ok",
        "imported": total_imported,
        "skipped": total_skipped,
        "errors": total_errors,
    }


async def _import_instagram(
    db, access_token: str, account_id: str, channel, tenant_id: uuid.UUID,
) -> tuple[int, int, list[str]]:
    """Fetch all media from Instagram and create post records."""
    from amplify.db.models.post import PostModel

    imported = 0
    skipped = 0
    errors: list[str] = []
    GRAPH_API = "https://graph.instagram.com/v21.0"

    async with httpx.AsyncClient(timeout=30) as client:
        url = f"{GRAPH_API}/me/media"
        params = {
            "fields": "id,caption,media_type,media_url,thumbnail_url,timestamp,permalink,like_count,comments_count",
            "access_token": access_token,
            "limit": "50",
        }

        while url:
            resp = await client.get(url, params=params)
            if resp.status_code >= 400:
                errors.append(f"Instagram API error: {resp.text[:200]}")
                break

            data = resp.json()
            for item in data.get("data", []):
                platform_post_id = item.get("id", "")

                existing = await db.execute(
                    select(PostModel.id).where(
                        PostModel.platform_post_id == platform_post_id,
                        PostModel.tenant_id == tenant_id,
                    )
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

                media_url = item.get("media_url") or item.get("thumbnail_url") or ""
                published_at = None
                if item.get("timestamp"):
                    try:
                        published_at = datetime.fromisoformat(
                            item["timestamp"].replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except Exception:
                        pass

                post = PostModel(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    channel_id=channel.id,
                    platform="instagram",
                    status="published",
                    content_text=item.get("caption", ""),
                    media_urls=[media_url] if media_url else [],
                    platform_post_id=platform_post_id,
                    permalink=item.get("permalink", ""),
                    published_at=published_at,
                    engagement={
                        "likes": item.get("like_count", 0),
                        "comments": item.get("comments_count", 0),
                    },
                    action_type_label=item.get("media_type", "IMAGE").lower(),
                )
                db.add(post)
                imported += 1

            next_url = data.get("paging", {}).get("next")
            if next_url:
                url = next_url
                params = {}
            else:
                break

    return imported, skipped, errors


async def _import_youtube(
    db, access_token: str, channel, tenant_id: uuid.UUID,
) -> tuple[int, int, list[str]]:
    """Fetch all videos from YouTube channel and create post records."""
    from amplify.db.models.post import PostModel

    imported = 0
    skipped = 0
    errors: list[str] = []
    YT_API = "https://www.googleapis.com/youtube/v3"
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=30) as client:
        ch_resp = await client.get(
            f"{YT_API}/channels",
            headers=headers,
            params={"part": "contentDetails", "mine": "true"},
        )
        if ch_resp.status_code >= 400:
            return 0, 0, [f"YouTube channels API error: {ch_resp.text[:200]}"]

        items = ch_resp.json().get("items", [])
        if not items:
            return 0, 0, ["No YouTube channel found"]

        uploads_playlist = (
            items[0].get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not uploads_playlist:
            return 0, 0, ["No uploads playlist found"]

        page_token = None
        video_ids: list[str] = []

        while True:
            params: dict = {
                "part": "snippet",
                "playlistId": uploads_playlist,
                "maxResults": "50",
            }
            if page_token:
                params["pageToken"] = page_token

            pl_resp = await client.get(
                f"{YT_API}/playlistItems", headers=headers, params=params
            )
            if pl_resp.status_code >= 400:
                errors.append(f"YouTube playlistItems error: {pl_resp.text[:200]}")
                break

            pl_data = pl_resp.json()
            for item in pl_data.get("items", []):
                vid_id = (
                    item.get("snippet", {}).get("resourceId", {}).get("videoId")
                )
                if vid_id:
                    video_ids.append(vid_id)

            page_token = pl_data.get("nextPageToken")
            if not page_token:
                break

        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            v_resp = await client.get(
                f"{YT_API}/videos",
                headers=headers,
                params={
                    "part": "snippet,statistics,status",
                    "id": ",".join(batch),
                },
            )
            if v_resp.status_code >= 400:
                errors.append(f"YouTube videos API error: {v_resp.text[:200]}")
                continue

            for v in v_resp.json().get("items", []):
                video_id = v.get("id", "")

                existing = await db.execute(
                    select(PostModel.id).where(
                        PostModel.platform_post_id == video_id,
                        PostModel.tenant_id == tenant_id,
                    )
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

                snippet = v.get("snippet", {})
                stats = v.get("statistics", {})
                published_at = None
                if snippet.get("publishedAt"):
                    try:
                        published_at = datetime.fromisoformat(
                            snippet["publishedAt"].replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except Exception:
                        pass

                post = PostModel(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    channel_id=channel.id,
                    platform="youtube",
                    status="published",
                    content_text=snippet.get("description", ""),
                    media_urls=[
                        snippet.get("thumbnails", {}).get("high", {}).get("url", "")
                    ],
                    platform_post_id=video_id,
                    permalink=f"https://youtu.be/{video_id}",
                    published_at=published_at,
                    engagement={
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                        # Title kept here so we don't lose it; the column
                        # action_type_label is a category enum (VARCHAR(50)),
                        # not a free-text field — see the IG branch above.
                        "title": snippet.get("title", ""),
                    },
                    action_type_label="video",
                )
                db.add(post)
                imported += 1

    return imported, skipped, errors
