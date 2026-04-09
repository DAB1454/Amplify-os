"""Destination validation and canonical CTA URL resolution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

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


# Phrases that indicate a CTA is already present in the caption — used to
# avoid double-injection when the planner already wrote one in the brief.
_BIO_LINK_MARKERS = (
    "link in bio",
    "link in my bio",
    "link in the bio",
    "bio link",
    "linkinbio",
    "link below",
    "check the bio",
)


def inject_caption_cta(caption: str, url: str, platform: str) -> str:
    """Embed the campaign CTA into the post caption based on platform mechanics.

    Captions on Instagram and TikTok do NOT render clickable links, so for those
    platforms we point users at the bio link instead of pasting the URL. YouTube
    descriptions DO render clickable links, so we paste the URL directly.

    Idempotent: if the caption already contains the URL (YouTube case) or any
    bio-link phrase (IG/TikTok case), the caption is returned unchanged so the
    planner's own CTA is preserved.
    """
    text = caption or ""
    if not url:
        return text

    plat = (platform or "").lower()
    lowered = text.lower()

    if plat == "youtube":
        if url in text:
            return text
        sep = "\n\n" if text.strip() else ""
        return f"{text}{sep}🎧 {url}"

    # Instagram, TikTok, and any other "caption is not a hyperlink" platform
    if any(marker in lowered for marker in _BIO_LINK_MARKERS):
        return text
    sep = "\n\n" if text.strip() else ""
    return f"{text}{sep}🎧 Link in bio"


# ── goal-aware CTA selection ─────────────────────────────────────


# Recognized marketing goals — keep in sync with PostModel.goal column
# and DailyAction.goal field. Empty/None means "not specified".
GOALS = ("awareness", "follow", "engage", "save", "stream", "purchase")


@dataclass
class CTASelection:
    """Result of CTA resolution for a single post.

    `url` is what we store on `posts.destination_url` (analytics + policy).
    `caption_snippet` is what gets injected into the caption text — for
    IG/TikTok this is "🎧 Link in bio" because captions aren't clickable;
    for YouTube it includes the URL because descriptions are.
    `bio_link_hint` is the URL the artist should set as their active bio
    link to make IG/TikTok captions actually convert. Surfaced to the user
    via the campaign approval UI.
    """

    url: str
    caption_snippet: str
    bio_link_hint: str
    goal: str


def cta_for(
    *,
    goal: str | None,
    platform: str,
    track: Any | None = None,
    release: Any | None = None,
    artist: Any | None = None,
) -> CTASelection:
    """Pick the right CTA URL + caption snippet for a (goal, platform, track) combo.

    Resolution rules (in priority order, first non-empty wins):

      stream    → track.spotify_url → track.apple_music_url → release.linktree_url → release.hyperfollow_url
      save      → release.hyperfollow_url → release.linktree_url
      purchase  → track.bandcamp_url → release.bandcamp_url → release.linktree_url
      follow    → artist.social_links[platform] → release platform URL → release.linktree_url
      engage    → "" (no link — engage posts are about replies/saves/shares, not clickthroughs)
      awareness → "" (top of funnel — no CTA pressure)
      None      → release.linktree_url (legacy fallback)

    Caller is responsible for storing `url` on the post and feeding the
    full caption through `inject_caption_cta(caption, url, platform)` —
    this function is pure and does no DB I/O.
    """
    plat = (platform or "").lower()
    g = (goal or "").lower().strip()

    def _attr(obj: Any, name: str) -> str:
        if obj is None:
            return ""
        return (getattr(obj, name, "") or "")

    spotify_t = _attr(track, "spotify_url")
    apple_t = _attr(track, "apple_music_url")
    bandcamp_t = _attr(track, "bandcamp_url")

    linktree_r = _attr(release, "linktree_url")
    hyperfollow_r = _attr(release, "hyperfollow_url")
    bandcamp_r = _attr(release, "bandcamp_url")
    youtube_r = _attr(release, "youtube_url")
    tiktok_r = _attr(release, "tiktok_url")
    instagram_r = _attr(release, "instagram_url")

    social = {}
    if artist is not None:
        social = getattr(artist, "social_links", None) or {}

    url = ""
    if g == "stream":
        url = spotify_t or apple_t or linktree_r or hyperfollow_r
    elif g == "save":
        url = hyperfollow_r or linktree_r
    elif g == "purchase":
        url = bandcamp_t or bandcamp_r or linktree_r
    elif g == "follow":
        # Prefer the artist's actual platform profile, fall back to release
        # platform field, then linktree as last resort.
        platform_field_map = {
            "instagram": (social.get("instagram", ""), instagram_r),
            "tiktok": (social.get("tiktok", ""), tiktok_r),
            "youtube": (social.get("youtube", ""), youtube_r),
        }
        first, second = platform_field_map.get(plat, ("", ""))
        url = first or second or linktree_r
    elif g in ("engage", "awareness"):
        url = ""  # intentional — no link pressure
    else:
        # Unspecified goal — preserve legacy behaviour
        url = linktree_r

    # Pick the bio-link hint independently of which DSP we picked.
    # The bio link should always go to the broadest aggregator so any
    # viewer can find their preferred service.
    bio_link_hint = linktree_r or hyperfollow_r or url

    # Build caption snippet based on platform mechanics
    if not url:
        snippet = ""
    elif plat == "youtube":
        snippet = f"🎧 {url}"
    else:
        # IG / TikTok / etc — captions aren't clickable
        snippet = "🎧 Link in bio"

    return CTASelection(
        url=url,
        caption_snippet=snippet,
        bio_link_hint=bio_link_hint,
        goal=g,
    )


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
