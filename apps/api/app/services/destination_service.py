"""Destination validation and canonical CTA URL resolution."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.campaign import CampaignModel
from amplify.db.models.post import PostModel
from amplify.db.models.release import ReleaseModel


# Preferred destination priority — first non-null wins as the canonical CTA
DESTINATION_PRIORITY = [
    "hyperfollow_url",
    "linktree_url",
    "bandcamp_url",
    "youtube_url",
    "instagram_url",
    "tiktok_url",
]

# Map platform name → best release destination field
PLATFORM_DESTINATION_MAP: dict[str, str] = {
    "instagram": "instagram_url",
    "tiktok": "tiktok_url",
    "youtube": "youtube_url",
    "bandcamp": "bandcamp_url",
}


class DestinationService:
    """Resolve canonical CTA URLs and validate post destinations."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def get_canonical_url(self, campaign_id: uuid.UUID) -> str | None:
        """Return the canonical CTA URL for a campaign.

        Resolves via the campaign's linked release destinations using priority order.
        Falls back to HyperFollow > Linktree > Bandcamp > YouTube > Instagram > TikTok.
        """
        stmt = (
            select(CampaignModel)
            .where(
                CampaignModel.id == campaign_id,
                CampaignModel.tenant_id == self.tenant_id,
            )
        )
        result = await self.session.execute(stmt)
        campaign = result.scalar_one_or_none()
        if campaign is None or campaign.release_id is None:
            return None

        stmt = (
            select(ReleaseModel)
            .where(
                ReleaseModel.id == campaign.release_id,
                ReleaseModel.tenant_id == self.tenant_id,
            )
        )
        result = await self.session.execute(stmt)
        release = result.scalar_one_or_none()
        if release is None:
            return None

        for field in DESTINATION_PRIORITY:
            url = getattr(release, field, None)
            if url:
                return url

        return None

    async def get_platform_url(
        self, campaign_id: uuid.UUID, platform: str
    ) -> str | None:
        """Return the best destination URL for a specific platform.

        Falls back to the canonical URL if no platform-specific destination exists.
        """
        stmt = (
            select(CampaignModel)
            .where(
                CampaignModel.id == campaign_id,
                CampaignModel.tenant_id == self.tenant_id,
            )
        )
        result = await self.session.execute(stmt)
        campaign = result.scalar_one_or_none()
        if campaign is None or campaign.release_id is None:
            return None

        stmt = (
            select(ReleaseModel)
            .where(
                ReleaseModel.id == campaign.release_id,
                ReleaseModel.tenant_id == self.tenant_id,
            )
        )
        result = await self.session.execute(stmt)
        release = result.scalar_one_or_none()
        if release is None:
            return None

        # Try platform-specific field first
        platform_field = PLATFORM_DESTINATION_MAP.get(platform)
        if platform_field:
            url = getattr(release, platform_field, None)
            if url:
                return url

        # Fall back to canonical priority
        for field in DESTINATION_PRIORITY:
            url = getattr(release, field, None)
            if url:
                return url

        return None

    async def validate_campaign_destinations(
        self, campaign_id: uuid.UUID
    ) -> dict:
        """Check that every scheduled post in a campaign has a destination URL.

        Returns a report dict with:
        - canonical_url: the campaign's resolved CTA
        - total_posts: count of all posts
        - posts_with_destination: count of posts that have a destination_url
        - posts_missing_destination: list of {post_id, platform, status, scheduled_at}
        """
        canonical = await self.get_canonical_url(campaign_id)

        stmt = (
            select(PostModel)
            .where(
                PostModel.campaign_id == campaign_id,
                PostModel.tenant_id == self.tenant_id,
            )
        )
        result = await self.session.execute(stmt)
        posts = list(result.scalars().all())

        missing = []
        with_dest = 0
        for post in posts:
            if post.destination_url:
                with_dest += 1
            else:
                missing.append(
                    {
                        "post_id": post.id,
                        "platform": post.platform,
                        "status": post.status,
                        "scheduled_at": post.scheduled_at,
                    }
                )

        return {
            "campaign_id": campaign_id,
            "canonical_url": canonical,
            "total_posts": len(posts),
            "posts_with_destination": with_dest,
            "posts_missing_destination": missing,
        }
