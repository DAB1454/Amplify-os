"""Service layer for offline evaluation runs.

Bridges the ReplayEngine (pure computation) with the database
(EvaluationRun / EvaluationResult models) and audit logging.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from amplify.db.models.learning import (
    EvaluationResultModel,
    EvaluationRunModel,
    LearningAuditLogModel,
    PostFeatureVectorModel,
    PostOutcomeModel,
)
from amplify.learning.evaluation.replay import (
    ComparisonReport,
    PolicyEvalReport,
    PostOpportunity,
    RankingPolicySpec,
    ReplayDataset,
    ReplayEngine,
)


class EvaluationService:
    """DB-backed evaluation service.

    Orchestrates replay evaluation, persists results, and provides
    query methods for the API layer.
    """

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID | None = None) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self._engine = ReplayEngine()

    # ── Run evaluation ─────────────────────────────────────────────

    async def run_evaluation(
        self,
        *,
        policies: list[RankingPolicySpec],
        dataset: ReplayDataset | None = None,
        evaluation_type: str = "ranking",
        scope: str = "tenant",
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        top_k: int = 3,
    ) -> EvaluationRunModel:
        """Run a replay evaluation and persist results.

        If no dataset is provided, builds one from stored feature vectors
        and outcomes for the tenant within the given time window.
        """
        started_at = datetime.now(timezone.utc)

        if dataset is None:
            if not self.tenant_id:
                raise ValueError("tenant_id required to build dataset from DB")
            dataset = await self._build_dataset_from_db(
                window_start=window_start,
                window_end=window_end,
            )

        # Run the replay engine
        report = self._engine.evaluate(dataset, policies, top_k=top_k)

        # Pick the first policy report for the run-level metrics
        primary = report.policies[0] if report.policies else None

        run = EvaluationRunModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id or uuid.UUID(int=0),
            evaluation_type=evaluation_type,
            scope=scope,
            window_start=window_start or datetime.min.replace(tzinfo=timezone.utc),
            window_end=window_end or datetime.now(timezone.utc),
            sample_size=report.dataset_size,
            mae=primary.mae if primary else None,
            rank_correlation=primary.rank_correlation if primary else None,
            top_k_precision=primary.top_k_precision if primary else None,
            top_k=top_k,
            details=report.to_dict(),
            status="completed",
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        await self.db.flush()

        # Persist per-post results for the primary policy
        if primary:
            for r in primary.results:
                result_row = EvaluationResultModel(
                    id=uuid.uuid4(),
                    evaluation_run_id=run.id,
                    action_id=None,
                    action_type="post_ranking",
                    predicted_score=r.predicted_score,
                    predicted_rank=r.predicted_rank,
                    actual_reward=r.actual_reward,
                    actual_rank=r.actual_rank,
                    absolute_error=r.absolute_error,
                    explanation={"factors": r.factors, "platform": r.platform},
                )
                self.db.add(result_row)
            await self.db.flush()

        await self._audit("evaluation.completed", "evaluation_run", run.id, {
            "evaluation_type": evaluation_type,
            "scope": scope,
            "sample_size": report.dataset_size,
            "policies": [p.policy_name for p in report.policies],
            "winner": report.winner,
        })

        return run

    # ── Query methods ──────────────────────────────────────────────

    async def get_run(self, run_id: uuid.UUID) -> EvaluationRunModel | None:
        stmt = (
            select(EvaluationRunModel)
            .where(EvaluationRunModel.id == run_id)
            .options(selectinload(EvaluationRunModel.results))
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_runs(
        self,
        evaluation_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EvaluationRunModel]:
        stmt = (
            select(EvaluationRunModel)
            .order_by(EvaluationRunModel.created_at.desc())
        )
        if self.tenant_id:
            stmt = stmt.where(EvaluationRunModel.tenant_id == self.tenant_id)
        if evaluation_type:
            stmt = stmt.where(EvaluationRunModel.evaluation_type == evaluation_type)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_results(
        self,
        run_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EvaluationResultModel]:
        stmt = (
            select(EvaluationResultModel)
            .where(EvaluationResultModel.evaluation_run_id == run_id)
            .order_by(EvaluationResultModel.predicted_rank)
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_comparison(self, run_id: uuid.UUID) -> dict[str, Any]:
        """Return the full ComparisonReport dict stored in run.details."""
        run = await self.get_run(run_id)
        if not run:
            return {}
        return run.details or {}

    # ── Dataset building ───────────────────────────────────────────

    async def _build_dataset_from_db(
        self,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> ReplayDataset:
        """Build a ReplayDataset from stored feature vectors and outcomes."""
        # Get feature vectors for this tenant
        fv_stmt = select(PostFeatureVectorModel).where(
            PostFeatureVectorModel.tenant_id == self.tenant_id
        )
        if window_start:
            fv_stmt = fv_stmt.where(PostFeatureVectorModel.extracted_at >= window_start)
        if window_end:
            fv_stmt = fv_stmt.where(PostFeatureVectorModel.extracted_at <= window_end)
        fv_result = await self.db.execute(fv_stmt)
        feature_vectors = {fv.post_id: fv for fv in fv_result.scalars().all()}

        if not feature_vectors:
            return ReplayDataset(name="db_empty", description="No feature vectors found")

        # Get outcomes for these posts (prefer "final" or "7d" windows)
        post_ids = list(feature_vectors.keys())
        outcome_stmt = (
            select(PostOutcomeModel)
            .where(
                PostOutcomeModel.tenant_id == self.tenant_id,
                PostOutcomeModel.post_id.in_(post_ids),
            )
            .order_by(PostOutcomeModel.measured_at.desc())
        )
        outcome_result = await self.db.execute(outcome_stmt)
        # Keep best outcome per post (latest measurement)
        outcomes: dict[uuid.UUID, PostOutcomeModel] = {}
        for o in outcome_result.scalars().all():
            if o.post_id not in outcomes:
                outcomes[o.post_id] = o

        # Build opportunities
        opportunities = []
        for post_id, fv in feature_vectors.items():
            outcome = outcomes.get(post_id)
            reward = outcome.reward if outcome else 0.0

            opportunities.append(PostOpportunity(
                post_id=str(post_id),
                platform=fv.platform or "",
                content_type=fv.content_type or "",
                post_hour=fv.post_hour,
                post_day_of_week=fv.post_day_of_week,
                caption_length=fv.caption_length,
                hashtag_count=fv.hashtag_count,
                has_link=fv.has_link,
                has_media=True,
                campaign_phase=fv.campaign_phase or "",
                actual_reward=reward or 0.0,
                actual_engagement_rate=(
                    outcome.engagement_rate if outcome else None
                ),
                actual_save_rate=outcome.save_rate if outcome else None,
                actual_click_through_rate=(
                    outcome.click_through_rate if outcome else None
                ),
                tenant_id=str(self.tenant_id),
                extra=fv.features or {},
            ))

        return ReplayDataset(
            opportunities=opportunities,
            name=f"tenant_{str(self.tenant_id)[:8]}",
            description=f"Auto-built from {len(opportunities)} stored feature vectors",
            window_start=window_start.isoformat() if window_start else "",
            window_end=window_end.isoformat() if window_end else "",
        )

    # ── Helpers ────────────────────────────────────────────────────

    async def _audit(
        self,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        details: dict,
    ) -> None:
        row = LearningAuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id or uuid.UUID(int=0),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            schema_version=1,
        )
        self.db.add(row)
