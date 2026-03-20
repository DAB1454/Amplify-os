"""Historical backfill engine for the learning pipeline.

Backfills PostFeatureVector, PostOutcome (reward computation), and
LearningEvent records from existing posts and engagement data.
Optionally bootstraps tenant patterns from the backfilled data.

Designed to be:
- **Resumable**: tracks progress via a cursor (last processed post_id).
- **Idempotent**: all services use upsert/idempotency-key semantics.
- **Isolated**: tagged with source="backfill" so live ingestion is unaffected.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import (
    LearningEventModel,
    PostFeatureVectorModel,
    PostOutcomeModel,
)
from amplify.db.models.post import PostModel

logger = logging.getLogger(__name__)

BACKFILL_SOURCE = "backfill"


@dataclass
class BackfillReport:
    """Structured report of a backfill run."""

    tenant_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    status: str = "pending"  # pending, running, completed, failed

    # Counts
    posts_scanned: int = 0
    features_created: int = 0
    features_skipped: int = 0
    features_errors: int = 0
    outcomes_created: int = 0
    outcomes_skipped: int = 0
    outcomes_errors: int = 0
    events_created: int = 0
    events_skipped: int = 0
    events_errors: int = 0
    patterns_created: int = 0

    # Resumability
    cursor: str | None = None
    is_complete: bool = False

    # Data quality
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def add_warning(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def add_error(self, post_id: str, stage: str, error: str) -> None:
        self.errors.append({"post_id": post_id, "stage": stage, "error": error})


class FeatureExtractor(Protocol):
    """Protocol for feature extraction service."""

    async def extract_and_persist(self, post_id: uuid.UUID) -> Any: ...


class OutcomeRecorder(Protocol):
    """Protocol for outcome recording service."""

    async def record_outcome(
        self,
        post_id: uuid.UUID,
        measurement_window: str,
        metrics: dict[str, Any],
        measured_at: datetime | None = ...,
        source: str = ...,
    ) -> Any: ...


class EventEmitter(Protocol):
    """Protocol for learning event emission."""

    async def emit(self, action_type: str, observed_at: datetime | None = ..., **kwargs: Any) -> Any: ...


class PatternUpdater(Protocol):
    """Protocol for pattern learning service."""

    async def get_data_status(self) -> dict: ...
    async def update_patterns(self) -> list: ...


class BackfillEngine:
    """Executes a learning backfill for a single tenant.

    Uses protocol-based dependency injection so it can be tested
    without importing from app.services.

    Usage::

        engine = BackfillEngine(db, tenant_id)
        engine.feature_extractor = FeatureExtractionService(db, tenant_id)
        engine.outcome_recorder = OutcomeService(db, tenant_id)
        engine.event_emitter = LearningEventService(db, tenant_id)
        engine.pattern_updater = TenantPatternService(db, tenant_id)
        report = await engine.run()
    """

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        dry_run: bool = False,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.dry_run = dry_run
        self.feature_extractor: FeatureExtractor | None = None
        self.outcome_recorder: OutcomeRecorder | None = None
        self.event_emitter: EventEmitter | None = None
        self.pattern_updater: PatternUpdater | None = None

    def _ensure_services(self) -> None:
        """Lazy-init services from app.services if not explicitly set."""
        if self.feature_extractor is None:
            from app.services.feature_extraction_service import FeatureExtractionService
            self.feature_extractor = FeatureExtractionService(self.db, self.tenant_id)
        if self.outcome_recorder is None:
            from app.services.outcome_service import OutcomeService
            self.outcome_recorder = OutcomeService(self.db, self.tenant_id)
        if self.event_emitter is None:
            from app.services.learning_event_service import LearningEventService
            self.event_emitter = LearningEventService(self.db, self.tenant_id)
        if self.pattern_updater is None:
            from app.services.tenant_pattern_service import TenantPatternService
            self.pattern_updater = TenantPatternService(self.db, self.tenant_id)

    async def run(
        self,
        *,
        batch_size: int = 200,
        skip_features: bool = False,
        skip_outcomes: bool = False,
        skip_events: bool = False,
        bootstrap_patterns: bool = True,
        cursor: str | None = None,
    ) -> BackfillReport:
        """Execute the full backfill pipeline."""
        self._ensure_services()

        report = BackfillReport(
            tenant_id=str(self.tenant_id),
            started_at=datetime.now(timezone.utc).isoformat(),
            status="running",
        )

        try:
            posts = await self._load_posts(batch_size, cursor)
            report.posts_scanned = len(posts)

            if not posts:
                report.add_warning("No posts found for backfill")
                report.status = "completed"
                report.is_complete = True
                report.completed_at = datetime.now(timezone.utc).isoformat()
                return report

            await self._check_data_quality(posts, report)

            if not skip_features:
                await self._backfill_features(posts, report)

            if not skip_outcomes:
                await self._backfill_outcomes(posts, report)

            if not skip_events:
                await self._backfill_events(posts, report)

            if not self.dry_run:
                await self.db.commit()

            if bootstrap_patterns and not self.dry_run:
                await self._bootstrap_patterns(report)
                await self.db.commit()

            last_post = posts[-1]
            report.cursor = str(last_post.id)
            remaining = await self._count_remaining_posts(str(last_post.id))
            report.is_complete = remaining == 0

            report.status = "completed"

        except Exception as exc:
            report.status = "failed"
            report.add_error("", "run", str(exc))
            logger.exception("Backfill failed for tenant %s", self.tenant_id)
            raise

        finally:
            report.completed_at = datetime.now(timezone.utc).isoformat()

        return report

    # ── Phase 1: Feature extraction ──────────────────────────────

    async def _backfill_features(
        self, posts: list[PostModel], report: BackfillReport,
    ) -> None:
        post_ids = [p.id for p in posts]
        existing = await self._existing_feature_post_ids(post_ids)

        assert self.feature_extractor is not None
        for post in posts:
            if post.id in existing:
                report.features_skipped += 1
                continue
            if self.dry_run:
                report.features_created += 1
                continue
            try:
                await self.feature_extractor.extract_and_persist(post.id)
                report.features_created += 1
            except Exception as exc:
                report.features_errors += 1
                report.add_error(str(post.id), "features", str(exc))
                logger.warning("Feature extraction failed for post %s: %s", post.id, exc)

    # ── Phase 2: Outcome backfill ────────────────────────────────

    async def _backfill_outcomes(
        self, posts: list[PostModel], report: BackfillReport,
    ) -> None:
        post_ids = [p.id for p in posts]
        existing_outcomes = await self._existing_outcome_post_ids(post_ids)

        assert self.outcome_recorder is not None
        for post in posts:
            if post.id in existing_outcomes:
                report.outcomes_skipped += 1
                continue
            engagement = post.engagement or {}
            if not engagement:
                report.outcomes_skipped += 1
                continue
            if self.dry_run:
                report.outcomes_created += 1
                continue
            try:
                window = self._infer_measurement_window(post)
                measured_at = post.published_at or post.created_at
                await self.outcome_recorder.record_outcome(
                    post_id=post.id,
                    measurement_window=window,
                    metrics=engagement,
                    measured_at=measured_at,
                    source=BACKFILL_SOURCE,
                )
                report.outcomes_created += 1
            except Exception as exc:
                report.outcomes_errors += 1
                report.add_error(str(post.id), "outcomes", str(exc))
                logger.warning("Outcome backfill failed for post %s: %s", post.id, exc)

    # ── Phase 3: Learning events ─────────────────────────────────

    async def _backfill_events(
        self, posts: list[PostModel], report: BackfillReport,
    ) -> None:
        assert self.event_emitter is not None
        for post in posts:
            vector = await self._get_feature_vector(post.id)
            outcome = await self._get_best_outcome(post.id)

            if vector is None or outcome is None:
                report.events_skipped += 1
                continue

            idem_key = f"backfill:post_published:{post.id}"
            if await self._event_exists(idem_key):
                report.events_skipped += 1
                continue
            if self.dry_run:
                report.events_created += 1
                continue
            try:
                await self.event_emitter.emit(
                    action_type="post_published",
                    observed_at=post.published_at or post.created_at,
                    idempotency_key=idem_key,
                    post_id=post.id,
                    campaign_id=post.campaign_id,
                    platform=post.platform,
                    features=vector.features,
                    outcomes=self._outcome_to_dict(outcome),
                    reward=outcome.reward,
                    reward_breakdown=outcome.reward_breakdown,
                    outcome_measured_at=outcome.measured_at,
                    source=BACKFILL_SOURCE,
                    confidence=0.8,
                    schema_version=vector.schema_version,
                )
                report.events_created += 1
            except Exception as exc:
                report.events_errors += 1
                report.add_error(str(post.id), "events", str(exc))
                logger.warning("Event backfill failed for post %s: %s", post.id, exc)

    # ── Phase 4: Pattern bootstrap ───────────────────────────────

    async def _bootstrap_patterns(self, report: BackfillReport) -> None:
        assert self.pattern_updater is not None
        status = await self.pattern_updater.get_data_status()
        if not status["has_enough_data"]:
            report.add_warning(
                f"Not enough data for pattern learning "
                f"({status['feature_vectors']} vectors, "
                f"need {status['min_required']})"
            )
            return
        try:
            patterns = await self.pattern_updater.update_patterns()
            report.patterns_created = len(patterns)
        except Exception as exc:
            report.add_warning(f"Pattern bootstrap failed: {exc}")
            logger.warning("Pattern bootstrap failed for tenant %s: %s", self.tenant_id, exc)

    # ── Data quality checks ──────────────────────────────────────

    async def _check_data_quality(
        self, posts: list[PostModel], report: BackfillReport,
    ) -> None:
        no_published_at = sum(1 for p in posts if p.published_at is None)
        if no_published_at:
            report.add_warning(
                f"{no_published_at} posts missing published_at "
                f"(will use created_at as fallback)"
            )

        no_engagement = sum(1 for p in posts if not (p.engagement or {}))
        if no_engagement:
            report.add_warning(
                f"{no_engagement}/{len(posts)} posts have no engagement data "
                f"(outcomes will be skipped)"
            )

        no_channel = sum(1 for p in posts if p.channel_id is None)
        if no_channel:
            report.add_warning(
                f"{no_channel} posts missing channel_id "
                f"(feature extraction may be incomplete)"
            )

    # ── DB helpers ───────────────────────────────────────────────

    async def _load_posts(
        self, batch_size: int, cursor: str | None,
    ) -> list[PostModel]:
        stmt = (
            select(PostModel)
            .where(
                PostModel.tenant_id == self.tenant_id,
                PostModel.status.in_(["published", "scheduled", "approved", "queued"]),
            )
            .order_by(PostModel.id)
            .limit(batch_size)
        )
        if cursor:
            stmt = stmt.where(PostModel.id > uuid.UUID(cursor))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _count_remaining_posts(self, after_cursor: str) -> int:
        stmt = (
            select(func.count())
            .select_from(PostModel)
            .where(
                PostModel.tenant_id == self.tenant_id,
                PostModel.status.in_(["published", "scheduled", "approved", "queued"]),
                PostModel.id > uuid.UUID(after_cursor),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def _existing_feature_post_ids(self, post_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not post_ids:
            return set()
        stmt = select(PostFeatureVectorModel.post_id).where(
            PostFeatureVectorModel.tenant_id == self.tenant_id,
            PostFeatureVectorModel.post_id.in_(post_ids),
        )
        result = await self.db.execute(stmt)
        return set(result.scalars().all())

    async def _existing_outcome_post_ids(self, post_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not post_ids:
            return set()
        stmt = (
            select(PostOutcomeModel.post_id)
            .where(
                PostOutcomeModel.tenant_id == self.tenant_id,
                PostOutcomeModel.post_id.in_(post_ids),
            )
            .distinct()
        )
        result = await self.db.execute(stmt)
        return set(result.scalars().all())

    async def _get_feature_vector(self, post_id: uuid.UUID) -> PostFeatureVectorModel | None:
        stmt = select(PostFeatureVectorModel).where(
            PostFeatureVectorModel.post_id == post_id,
            PostFeatureVectorModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_best_outcome(self, post_id: uuid.UUID) -> PostOutcomeModel | None:
        stmt = (
            select(PostOutcomeModel)
            .where(
                PostOutcomeModel.post_id == post_id,
                PostOutcomeModel.tenant_id == self.tenant_id,
                PostOutcomeModel.reward.is_not(None),
            )
        )
        result = await self.db.execute(stmt)
        outcomes = list(result.scalars().all())
        if not outcomes:
            return None
        priority = {"7d": 6, "final": 5, "48h": 4, "24h": 3, "3h": 2, "1h": 1}
        return max(outcomes, key=lambda o: priority.get(o.measurement_window, 0))

    async def _event_exists(self, idempotency_key: str) -> bool:
        stmt = select(func.count()).select_from(LearningEventModel).where(
            LearningEventModel.idempotency_key == idempotency_key
        )
        result = await self.db.execute(stmt)
        return (result.scalar() or 0) > 0

    @staticmethod
    def _infer_measurement_window(post: PostModel) -> str:
        if post.published_at is None:
            return "final"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age_hours = (now - post.published_at).total_seconds() / 3600
        if age_hours >= 168:
            return "7d"
        if age_hours >= 48:
            return "48h"
        if age_hours >= 24:
            return "24h"
        return "final"

    @staticmethod
    def _outcome_to_dict(outcome: PostOutcomeModel) -> dict[str, Any]:
        return {
            "impressions": outcome.impressions,
            "reach": outcome.reach,
            "engagements": outcome.engagements,
            "saves": outcome.saves,
            "shares": outcome.shares,
            "clicks": outcome.clicks,
            "comments_count": outcome.comments_count,
            "followers_gained": outcome.followers_gained,
            "engagement_rate": outcome.engagement_rate,
            "save_rate": outcome.save_rate,
            "click_through_rate": outcome.click_through_rate,
        }
