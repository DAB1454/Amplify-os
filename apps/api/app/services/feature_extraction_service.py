"""Feature extraction service — loads entities and persists PostFeatureVector.

Supports two modes:
1. Live extraction: called when a post is published/scheduled, loads related
   entities from the DB, runs the pipeline, and upserts a PostFeatureVector row.
2. Backfill: accepts a list of post IDs, extracts features for each, and
   upserts in bulk.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.asset import AssetModel, AssetVariantModel
from amplify.db.models.artist import ArtistModel
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.learning import PostFeatureVectorModel
from amplify.db.models.post import PostModel
from amplify.db.models.release import ReleaseModel
from amplify.db.models.tenant import TenantModel
from amplify.learning.feature_extraction.pipeline import (
    AssetContext,
    CampaignContext,
    ChannelContext,
    EarlyMetricsContext,
    PolicyContext,
    PostContext,
    ReleaseContext,
    SequenceContext,
    TenantContext,
    extract_features,
    SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v")


def _derive_media_format(post: PostModel, asset: AssetModel | None) -> str | None:
    """Classify a post's media into a learning-friendly format label.

    The clip-vs-static distinction is the reason this exists. ``clip_id`` is
    the clean discriminator — a post generated from a VideoClipModel — because
    its stitched-video S3 URL is not an AssetModel row and would otherwise be
    misread as a static image. Falls back to the matched asset's type, then to
    URL/extension inference. Returns None only for empty posts (no media, no
    text), letting the content extractor's own inference stand.
    """
    if post.clip_id is not None:
        return "clip"

    if asset and asset.asset_type:
        at = asset.asset_type.lower()
        if at in ("lyric_video", "video", "teaser"):
            return at
        if at in ("image", "promo_photo", "cover_art"):
            return "image"

    urls = post.media_urls or []
    if not urls:
        return "text" if post.content_text else None

    first = urls[0].lower()
    if any(first.endswith(ext) for ext in _VIDEO_EXTENSIONS):
        return "video"
    return "carousel" if len(urls) > 1 else "image"


class FeatureExtractionService:
    """Loads post context from DB, runs the extraction pipeline, and persists."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def extract_and_persist(
        self,
        post_id: uuid.UUID,
        early_metrics: EarlyMetricsContext | None = None,
    ) -> PostFeatureVectorModel:
        """Extract features for a single post and upsert the feature vector.

        If a PostFeatureVector already exists for this post_id, it is updated.
        """
        ctx = await self._build_context(post_id, early_metrics=early_metrics)
        features = extract_features(ctx)

        return await self._upsert_vector(post_id, ctx, features)

    async def backfill(
        self,
        post_ids: list[uuid.UUID] | None = None,
        limit: int = 500,
    ) -> dict:
        """Extract features for multiple posts (backfill mode).

        If post_ids is None, finds posts that don't have a feature vector yet.
        Returns summary with created/updated/error counts.
        """
        if post_ids is None:
            post_ids = await self._find_posts_without_vectors(limit)

        created = 0
        updated = 0
        errors: list[dict] = []

        for pid in post_ids:
            try:
                ctx = await self._build_context(pid)
                features = extract_features(ctx)
                vector = await self._upsert_vector(pid, ctx, features)
                if vector.created_at == vector.updated_at:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                logger.warning("Feature extraction failed for post %s: %s", pid, exc)
                errors.append({"post_id": str(pid), "error": str(exc)})

        return {
            "created": created,
            "updated": updated,
            "errors": errors,
            "total": len(post_ids),
        }

    async def get_vector(self, post_id: uuid.UUID) -> PostFeatureVectorModel | None:
        """Return the existing feature vector for a post, if any."""
        stmt = select(PostFeatureVectorModel).where(
            PostFeatureVectorModel.post_id == post_id,
            PostFeatureVectorModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ── Context building ──────────────────────────────────────────

    async def _build_context(
        self,
        post_id: uuid.UUID,
        early_metrics: EarlyMetricsContext | None = None,
    ) -> PostContext:
        """Load all related entities and assemble a PostContext."""
        post = await self._load_post(post_id)
        if post is None:
            raise ValueError(f"Post {post_id} not found for tenant {self.tenant_id}")

        ctx = PostContext(
            content_text=post.content_text or "",
            media_urls=post.media_urls or [],
            published_at=post.published_at,
            scheduled_at=post.scheduled_at,
            early_metrics=early_metrics,
        )

        # Channel
        channel = await self._load_channel(post.channel_id)
        if channel:
            ctx.channel = ChannelContext(
                platform=channel.platform or "",
                integration_mode=channel.integration_mode,
                account_type=channel.account_type,
                capabilities=channel.capabilities or {},
            )

        # Campaign + release
        if post.campaign_id:
            campaign = await self._load_campaign(post.campaign_id)
            if campaign:
                ctx.campaign = CampaignContext(
                    phase=campaign.phase,
                    objective=None,
                    goals=campaign.goals or {},
                    release_date=None,
                    release_type=None,
                    track_count=None,
                    has_upc=None,
                )

                # Load release via campaign
                if campaign.release_id:
                    release = await self._load_release(campaign.release_id)
                    if release:
                        ctx.campaign.release_date = release.release_date
                        ctx.campaign.release_type = release.release_type
                        ctx.campaign.has_upc = bool(release.upc)

                        # Artist genre from release's artist
                        artist = await self._load_artist(release.artist_id)
                        if artist:
                            ctx.release = ReleaseContext(genre=artist.genre or None)

                        # Track count
                        ctx.campaign.track_count = await self._count_tracks(release.id)

        # Tenant
        tenant = await self._load_tenant()
        if tenant:
            ctx.tenant = TenantContext(
                plan_tier=tenant.plan_tier,
                onboarding_completed=tenant.onboarding_completed,
            )

        # Asset — pick the first media_url and try to match an asset
        asset = None
        if post.media_urls:
            asset = await self._find_asset_by_url(post.media_urls[0])
            if asset:
                variant = await self._find_best_variant(asset.id, post.platform)
                ctx.asset = AssetContext(
                    asset_type=asset.asset_type,
                    width=variant.width if variant else None,
                    height=variant.height if variant else None,
                    duration_seconds=variant.duration_seconds if variant else None,
                    file_size_bytes=asset.file_size_bytes,
                    mime_type=asset.mime_type,
                )

        # Media format — the key signal for the clip-vs-static learning
        # experiment. A clip post carries a clip_id FK, and its stitched-video
        # URL is NOT an AssetModel row, so _find_asset_by_url misses it and the
        # content extractor would otherwise mislabel the post "image" (the same
        # tag a static photo gets). Deriving media_type here makes clip posts
        # land as content_type="clip", which is the whole point of the
        # AMPLIFY_CLIP_SHARE mix in the content pipeline.
        ctx.media_type = _derive_media_format(post, asset)

        # Sequence position
        if post.campaign_id:
            seq = await self._compute_sequence(post)
            ctx.sequence = seq

        # Policy
        ctx.policy = PolicyContext(
            policy_decision=post.policy_decision,
            approval_required=post.policy_decision == "require_approval",
        )

        return ctx

    # ── DB loaders ────────────────────────────────────────────────

    async def _load_post(self, post_id: uuid.UUID) -> PostModel | None:
        stmt = select(PostModel).where(
            PostModel.id == post_id,
            PostModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_channel(self, channel_id: uuid.UUID) -> ChannelConnectionModel | None:
        stmt = select(ChannelConnectionModel).where(
            ChannelConnectionModel.id == channel_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_campaign(self, campaign_id: uuid.UUID) -> CampaignModel | None:
        stmt = select(CampaignModel).where(
            CampaignModel.id == campaign_id,
            CampaignModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_release(self, release_id: uuid.UUID) -> ReleaseModel | None:
        stmt = select(ReleaseModel).where(
            ReleaseModel.id == release_id,
            ReleaseModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_artist(self, artist_id: uuid.UUID) -> ArtistModel | None:
        stmt = select(ArtistModel).where(
            ArtistModel.id == artist_id,
            ArtistModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_tenant(self) -> TenantModel | None:
        stmt = select(TenantModel).where(TenantModel.id == self.tenant_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _count_tracks(self, release_id: uuid.UUID) -> int:
        from amplify.db.models.track import TrackModel
        stmt = select(func.count()).where(TrackModel.release_id == release_id)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def _find_asset_by_url(self, url: str) -> AssetModel | None:
        stmt = select(AssetModel).where(
            AssetModel.tenant_id == self.tenant_id,
            AssetModel.file_url == url,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_best_variant(
        self, asset_id: uuid.UUID, platform: str,
    ) -> AssetVariantModel | None:
        # Try platform-specific variant first, then any variant
        stmt = select(AssetVariantModel).where(
            AssetVariantModel.asset_id == asset_id,
            AssetVariantModel.platform == platform,
        )
        result = await self.db.execute(stmt)
        variant = result.scalar_one_or_none()
        if variant:
            return variant

        stmt = select(AssetVariantModel).where(
            AssetVariantModel.asset_id == asset_id,
        ).limit(1)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _compute_sequence(self, post: PostModel) -> SequenceContext:
        """Compute the sequence position of a post within its campaign."""
        # Count total posts in this campaign
        total_stmt = select(func.count()).where(
            PostModel.campaign_id == post.campaign_id,
            PostModel.tenant_id == self.tenant_id,
        )
        total_result = await self.db.execute(total_stmt)
        total = total_result.scalar() or 0

        # Position = count of posts created before this one in the same campaign
        position_stmt = select(func.count()).where(
            PostModel.campaign_id == post.campaign_id,
            PostModel.tenant_id == self.tenant_id,
            PostModel.created_at <= post.created_at,
        )
        position_result = await self.db.execute(position_stmt)
        position = position_result.scalar() or 1

        # Posts in last 24h (same tenant)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
        recent_stmt = select(func.count()).where(
            PostModel.tenant_id == self.tenant_id,
            PostModel.published_at >= cutoff,
            PostModel.id != post.id,
        )
        recent_result = await self.db.execute(recent_stmt)
        recent = recent_result.scalar() or 0

        return SequenceContext(
            position=position,
            total_in_campaign=total,
            prior_post_count_24h=recent,
        )

    async def _find_posts_without_vectors(self, limit: int) -> list[uuid.UUID]:
        """Find posts that don't have a feature vector yet."""
        subq = select(PostFeatureVectorModel.post_id).where(
            PostFeatureVectorModel.tenant_id == self.tenant_id,
        )
        stmt = (
            select(PostModel.id)
            .where(
                PostModel.tenant_id == self.tenant_id,
                PostModel.status.in_(["published", "scheduled", "approved", "queued"]),
                PostModel.id.notin_(subq),
            )
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── Persistence ───────────────────────────────────────────────

    async def _upsert_vector(
        self,
        post_id: uuid.UUID,
        ctx: PostContext,
        features: dict,
    ) -> PostFeatureVectorModel:
        """Create or update a PostFeatureVector row."""
        stmt = select(PostFeatureVectorModel).where(
            PostFeatureVectorModel.post_id == post_id,
            PostFeatureVectorModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        if existing:
            existing.features = features
            existing.platform = features.get("platform")
            existing.content_type = features.get("content_type")
            existing.post_hour = features.get("post_hour")
            existing.post_day_of_week = features.get("post_day_of_week")
            existing.caption_length = features.get("caption_length")
            existing.hashtag_count = features.get("hashtag_count")
            existing.has_link = features.get("has_link")
            existing.campaign_phase = features.get("campaign_phase") or features.get("release_phase")
            existing.extracted_at = now
            existing.schema_version = SCHEMA_VERSION
            await self.db.flush()
            return existing

        # Derive artist_id and campaign_id from context if available
        artist_id = None
        campaign_id = None
        post = await self._load_post(post_id)
        if post and post.campaign_id:
            campaign_id = post.campaign_id
            campaign = await self._load_campaign(post.campaign_id)
            if campaign:
                artist_id = campaign.artist_id

        vector = PostFeatureVectorModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            post_id=post_id,
            artist_id=artist_id,
            campaign_id=campaign_id,
            features=features,
            platform=features.get("platform"),
            content_type=features.get("content_type"),
            post_hour=features.get("post_hour"),
            post_day_of_week=features.get("post_day_of_week"),
            caption_length=features.get("caption_length"),
            hashtag_count=features.get("hashtag_count"),
            has_link=features.get("has_link"),
            campaign_phase=features.get("campaign_phase") or features.get("release_phase"),
            extracted_at=now,
            schema_version=SCHEMA_VERSION,
        )
        self.db.add(vector)
        await self.db.flush()
        await self.db.refresh(vector)
        return vector
