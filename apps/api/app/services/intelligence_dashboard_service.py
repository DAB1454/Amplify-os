"""Intelligence dashboard service.

Aggregates data across the learning subsystem for operator visibility:
- Tenant patterns with evidence and confidence
- Global priors with sample sizes
- Reward trends over time
- Post drill-down to learning signals
- Audit trail for suspicious/low-confidence actions
- Prompt version performance
- Evaluation run summaries
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, and_, or_, case, desc
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import (
    EvaluationRunModel,
    GlobalPatternStatModel,
    LearningAuditLogModel,
    LearningEventModel,
    PostFeatureVectorModel,
    PostOutcomeModel,
    PromptAssignmentModel,
    PromptVersionModel,
    TenantPatternModel,
)


class IntelligenceDashboardService:
    """Aggregates learning data for the intelligence dashboard."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    # ── Overview ──────────────────────────────────────────────────

    async def get_overview(self) -> dict[str, Any]:
        """High-level intelligence health metrics for the tenant."""
        pattern_count = await self.db.scalar(
            select(func.count(TenantPatternModel.id)).where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
            )
        )
        vector_count = await self.db.scalar(
            select(func.count(PostFeatureVectorModel.id)).where(
                PostFeatureVectorModel.tenant_id == self.tenant_id,
            )
        )
        outcome_count = await self.db.scalar(
            select(func.count(PostOutcomeModel.id)).where(
                PostOutcomeModel.tenant_id == self.tenant_id,
            )
        )
        event_count = await self.db.scalar(
            select(func.count(LearningEventModel.id)).where(
                LearningEventModel.tenant_id == self.tenant_id,
            )
        )
        eval_count = await self.db.scalar(
            select(func.count(EvaluationRunModel.id)).where(
                EvaluationRunModel.tenant_id == self.tenant_id,
            )
        )
        global_prior_count = await self.db.scalar(
            select(func.count(GlobalPatternStatModel.id)).where(
                GlobalPatternStatModel.is_active == True,  # noqa: E712
            )
        )
        low_confidence_count = await self.db.scalar(
            select(func.count(TenantPatternModel.id)).where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
                TenantPatternModel.confidence < 0.3,
            )
        )

        return {
            "active_patterns": pattern_count or 0,
            "feature_vectors": vector_count or 0,
            "outcomes": outcome_count or 0,
            "learning_events": event_count or 0,
            "evaluation_runs": eval_count or 0,
            "global_priors_available": global_prior_count or 0,
            "low_confidence_patterns": low_confidence_count or 0,
        }

    # ── Tenant Patterns ──────────────────────────────────────────

    async def get_tenant_patterns(
        self,
        *,
        pattern_type: str | None = None,
        direction: str | None = None,
        artist_id: uuid.UUID | None = None,
        min_confidence: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List tenant patterns with full evidence for dashboard display."""
        stmt = (
            select(TenantPatternModel)
            .where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
            )
            .order_by(TenantPatternModel.confidence.desc())
            .offset(offset)
            .limit(limit)
        )
        if pattern_type:
            stmt = stmt.where(TenantPatternModel.pattern_type == pattern_type)
        if direction:
            stmt = stmt.where(TenantPatternModel.direction == direction)
        if artist_id:
            stmt = stmt.where(TenantPatternModel.artist_id == artist_id)
        if min_confidence is not None:
            stmt = stmt.where(TenantPatternModel.confidence >= min_confidence)

        result = await self.db.execute(stmt)
        patterns = list(result.scalars().all())

        count_stmt = (
            select(func.count(TenantPatternModel.id))
            .where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
            )
        )
        total = await self.db.scalar(count_stmt) or 0

        return {
            "patterns": [self._pattern_to_dict(p) for p in patterns],
            "total": total,
        }

    # ── Global Priors ────────────────────────────────────────────

    async def get_global_priors(
        self,
        *,
        category: str | None = None,
        min_tenant_count: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List global priors with sample sizes and confidence intervals."""
        stmt = (
            select(GlobalPatternStatModel)
            .where(GlobalPatternStatModel.is_active == True)  # noqa: E712
            .order_by(GlobalPatternStatModel.mean_reward.desc())
            .offset(offset)
            .limit(limit)
        )
        if category:
            stmt = stmt.where(GlobalPatternStatModel.category == category)
        if min_tenant_count is not None:
            stmt = stmt.where(GlobalPatternStatModel.tenant_count >= min_tenant_count)

        result = await self.db.execute(stmt)
        priors = list(result.scalars().all())

        count_stmt = select(func.count(GlobalPatternStatModel.id)).where(
            GlobalPatternStatModel.is_active == True,  # noqa: E712
        )
        total = await self.db.scalar(count_stmt) or 0

        return {
            "priors": [self._global_prior_to_dict(p) for p in priors],
            "total": total,
        }

    # ── Reward Trends ────────────────────────────────────────────

    async def get_reward_trends(
        self,
        *,
        days: int = 30,
        platform: str | None = None,
        artist_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Reward trends over time, grouped by day."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        stmt = (
            select(
                func.date(PostOutcomeModel.measured_at).label("day"),
                func.avg(PostOutcomeModel.reward).label("avg_reward"),
                func.count(PostOutcomeModel.id).label("count"),
                func.min(PostOutcomeModel.reward).label("min_reward"),
                func.max(PostOutcomeModel.reward).label("max_reward"),
            )
            .where(
                PostOutcomeModel.tenant_id == self.tenant_id,
                PostOutcomeModel.measured_at >= cutoff,
                PostOutcomeModel.reward.is_not(None),
            )
            .group_by(func.date(PostOutcomeModel.measured_at))
            .order_by(func.date(PostOutcomeModel.measured_at))
        )

        # Join feature vectors for platform/artist/campaign filtering
        if platform or artist_id or campaign_id:
            stmt = stmt.join(
                PostFeatureVectorModel,
                and_(
                    PostOutcomeModel.post_id == PostFeatureVectorModel.post_id,
                    PostOutcomeModel.tenant_id == PostFeatureVectorModel.tenant_id,
                ),
            )
            if platform:
                stmt = stmt.where(PostFeatureVectorModel.platform == platform)
            if artist_id:
                stmt = stmt.where(PostFeatureVectorModel.artist_id == artist_id)
            if campaign_id:
                stmt = stmt.where(PostFeatureVectorModel.campaign_id == campaign_id)

        result = await self.db.execute(stmt)
        rows = result.all()

        return {
            "trend": [
                {
                    "date": str(r.day),
                    "avg_reward": round(float(r.avg_reward), 4) if r.avg_reward else 0,
                    "count": r.count,
                    "min_reward": round(float(r.min_reward), 4) if r.min_reward else 0,
                    "max_reward": round(float(r.max_reward), 4) if r.max_reward else 0,
                }
                for r in rows
            ],
            "days": days,
            "total_points": len(rows),
        }

    # ── Post Drill-Down ──────────────────────────────────────────

    async def get_post_intelligence(self, post_id: uuid.UUID) -> dict[str, Any]:
        """Full drill-down: features, outcomes, events, and matching patterns for a post."""
        # Feature vector
        fv_result = await self.db.execute(
            select(PostFeatureVectorModel).where(
                PostFeatureVectorModel.post_id == post_id,
                PostFeatureVectorModel.tenant_id == self.tenant_id,
            )
        )
        fv = fv_result.scalar_one_or_none()

        # All outcomes
        outcome_result = await self.db.execute(
            select(PostOutcomeModel)
            .where(
                PostOutcomeModel.post_id == post_id,
                PostOutcomeModel.tenant_id == self.tenant_id,
            )
            .order_by(PostOutcomeModel.measured_at)
        )
        outcomes = list(outcome_result.scalars().all())

        # Learning events referencing this post
        event_result = await self.db.execute(
            select(LearningEventModel)
            .where(
                LearningEventModel.post_id == post_id,
                LearningEventModel.tenant_id == self.tenant_id,
            )
            .order_by(LearningEventModel.observed_at)
        )
        events = list(event_result.scalars().all())

        # Find matching tenant patterns (features that match this post's features)
        matching_patterns: list[dict] = []
        if fv and fv.features:
            for feat_name, feat_value in fv.features.items():
                pattern_result = await self.db.execute(
                    select(TenantPatternModel).where(
                        TenantPatternModel.tenant_id == self.tenant_id,
                        TenantPatternModel.is_active == True,  # noqa: E712
                        TenantPatternModel.subject_feature == feat_name,
                        TenantPatternModel.subject_value == str(feat_value),
                    )
                )
                for p in pattern_result.scalars().all():
                    matching_patterns.append(self._pattern_to_dict(p))

        # Find matching global priors
        matching_priors: list[dict] = []
        if fv and fv.features:
            for feat_name, feat_value in fv.features.items():
                prior_result = await self.db.execute(
                    select(GlobalPatternStatModel).where(
                        GlobalPatternStatModel.is_active == True,  # noqa: E712
                        GlobalPatternStatModel.feature_name == feat_name,
                        GlobalPatternStatModel.feature_value == str(feat_value),
                    )
                )
                for p in prior_result.scalars().all():
                    matching_priors.append(self._global_prior_to_dict(p))

        # Audit log entries for this post
        audit_result = await self.db.execute(
            select(LearningAuditLogModel)
            .where(
                LearningAuditLogModel.tenant_id == self.tenant_id,
                LearningAuditLogModel.entity_id == post_id,
            )
            .order_by(LearningAuditLogModel.created_at)
        )
        audit_entries = list(audit_result.scalars().all())

        return {
            "post_id": str(post_id),
            "features": self._feature_vector_to_dict(fv) if fv else None,
            "outcomes": [self._outcome_to_dict(o) for o in outcomes],
            "learning_events": [self._event_to_dict(e) for e in events],
            "matching_patterns": matching_patterns,
            "matching_global_priors": matching_priors,
            "audit_trail": [self._audit_to_dict(a) for a in audit_entries],
        }

    # ── Evaluation Runs ──────────────────────────────────────────

    async def get_evaluation_summary(
        self,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Evaluation runs with accuracy metrics for the dashboard."""
        stmt = (
            select(EvaluationRunModel)
            .where(EvaluationRunModel.tenant_id == self.tenant_id)
            .order_by(EvaluationRunModel.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        runs = list(result.scalars().all())

        return {
            "runs": [
                {
                    "id": str(r.id),
                    "evaluation_type": r.evaluation_type,
                    "scope": r.scope,
                    "window_start": r.window_start.isoformat() if r.window_start else None,
                    "window_end": r.window_end.isoformat() if r.window_end else None,
                    "sample_size": r.sample_size,
                    "mae": round(r.mae, 4) if r.mae is not None else None,
                    "rank_correlation": (
                        round(r.rank_correlation, 4) if r.rank_correlation is not None else None
                    ),
                    "top_k_precision": (
                        round(r.top_k_precision, 4) if r.top_k_precision is not None else None
                    ),
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in runs
            ],
            "total": len(runs),
        }

    # ── Prompt Performance ───────────────────────────────────────

    async def get_prompt_performance(self) -> dict[str, Any]:
        """Prompt version assignments and their associated outcomes."""
        stmt = (
            select(PromptAssignmentModel)
            .where(
                PromptAssignmentModel.tenant_id == self.tenant_id,
                PromptAssignmentModel.is_active == True,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        assignments = list(result.scalars().all())

        versions: list[dict] = []
        for a in assignments:
            ver_result = await self.db.execute(
                select(PromptVersionModel).where(
                    PromptVersionModel.id == a.prompt_version_id
                )
            )
            ver = ver_result.scalar_one_or_none()
            if not ver:
                continue

            # Count events that occurred while this prompt was active
            event_count = await self.db.scalar(
                select(func.count(LearningEventModel.id)).where(
                    LearningEventModel.tenant_id == self.tenant_id,
                    LearningEventModel.observed_at >= a.created_at,
                )
            )
            avg_reward = await self.db.scalar(
                select(func.avg(LearningEventModel.reward)).where(
                    LearningEventModel.tenant_id == self.tenant_id,
                    LearningEventModel.observed_at >= a.created_at,
                    LearningEventModel.reward.is_not(None),
                )
            )

            versions.append({
                "prompt_key": a.prompt_key,
                "version": ver.version,
                "description": ver.description,
                "model_id": ver.model_id,
                "assigned_at": a.created_at.isoformat() if a.created_at else None,
                "event_count": event_count or 0,
                "avg_reward": round(float(avg_reward), 4) if avg_reward else None,
            })

        return {"prompt_versions": versions}

    # ── Operator Audit ───────────────────────────────────────────

    async def get_audit_trail(
        self,
        *,
        action: str | None = None,
        entity_type: str | None = None,
        days: int = 30,
        suspicious_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Audit trail with filters. suspicious_only surfaces low-confidence actions."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        stmt = (
            select(LearningAuditLogModel)
            .where(
                LearningAuditLogModel.tenant_id == self.tenant_id,
                LearningAuditLogModel.created_at >= cutoff,
            )
            .order_by(LearningAuditLogModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if action:
            stmt = stmt.where(LearningAuditLogModel.action == action)
        if entity_type:
            stmt = stmt.where(LearningAuditLogModel.entity_type == entity_type)

        result = await self.db.execute(stmt)
        entries = list(result.scalars().all())

        items = [self._audit_to_dict(e) for e in entries]

        if suspicious_only:
            items = [
                i for i in items
                if self._is_suspicious(i)
            ]

        return {
            "entries": items,
            "total": len(items),
            "filters": {
                "action": action,
                "entity_type": entity_type,
                "days": days,
                "suspicious_only": suspicious_only,
            },
        }

    async def get_suspicious_actions(
        self,
        *,
        days: int = 7,
    ) -> dict[str, Any]:
        """Surface learning actions that warrant operator attention.

        Flags:
        - Low-confidence patterns (< 0.3)
        - Patterns with very few observations (< 5)
        - Rapid version changes (patterns updated > 3 times)
        - Anomalous reward values
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        alerts: list[dict] = []

        # Low-confidence active patterns
        low_conf_result = await self.db.execute(
            select(TenantPatternModel).where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
                TenantPatternModel.confidence < 0.3,
            )
        )
        for p in low_conf_result.scalars().all():
            alerts.append({
                "type": "low_confidence_pattern",
                "severity": "warning",
                "entity_type": "pattern",
                "entity_id": str(p.id),
                "message": (
                    f"Pattern '{p.title}' has low confidence ({p.confidence:.2f}). "
                    f"Based on {p.supporting_observation_count} observations."
                ),
                "confidence": p.confidence,
                "observation_count": p.supporting_observation_count,
            })

        # Patterns with very few observations but high confidence (potential overfit)
        thin_result = await self.db.execute(
            select(TenantPatternModel).where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
                TenantPatternModel.supporting_observation_count < 5,
                TenantPatternModel.confidence >= 0.7,
            )
        )
        for p in thin_result.scalars().all():
            alerts.append({
                "type": "thin_data_high_confidence",
                "severity": "warning",
                "entity_type": "pattern",
                "entity_id": str(p.id),
                "message": (
                    f"Pattern '{p.title}' has high confidence ({p.confidence:.2f}) "
                    f"but only {p.supporting_observation_count} observations. May be overfitting."
                ),
                "confidence": p.confidence,
                "observation_count": p.supporting_observation_count,
            })

        # Rapidly changing patterns (version > 3 in recent window)
        rapid_result = await self.db.execute(
            select(TenantPatternModel).where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
                TenantPatternModel.version > 3,
                TenantPatternModel.updated_at >= cutoff,
            )
        )
        for p in rapid_result.scalars().all():
            alerts.append({
                "type": "rapid_pattern_change",
                "severity": "info",
                "entity_type": "pattern",
                "entity_id": str(p.id),
                "message": (
                    f"Pattern '{p.title}' has been updated {p.version} times. "
                    f"May indicate unstable learning signal."
                ),
                "version": p.version,
            })

        # Anomalous rewards (outside normal range)
        anomaly_result = await self.db.execute(
            select(PostOutcomeModel).where(
                PostOutcomeModel.tenant_id == self.tenant_id,
                PostOutcomeModel.measured_at >= cutoff,
                PostOutcomeModel.reward.is_not(None),
                or_(
                    PostOutcomeModel.reward > 1.0,
                    PostOutcomeModel.reward < 0.0,
                ),
            ).limit(20)
        )
        for o in anomaly_result.scalars().all():
            alerts.append({
                "type": "anomalous_reward",
                "severity": "warning",
                "entity_type": "outcome",
                "entity_id": str(o.id),
                "message": (
                    f"Post {o.post_id} has reward {o.reward:.4f} "
                    f"(outside 0-1 range) at window '{o.measurement_window}'."
                ),
                "reward": o.reward,
            })

        # Sort by severity
        severity_order = {"error": 0, "warning": 1, "info": 2}
        alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))

        return {
            "alerts": alerts,
            "total": len(alerts),
            "period_days": days,
        }

    # ── Ranking Decisions ────────────────────────────────────────

    async def get_ranking_decisions(
        self,
        *,
        days: int = 7,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Recent ranking/scoring audit log entries with explanations."""
        cutoff = datetime.utcnow() - timedelta(days=days)

        stmt = (
            select(LearningAuditLogModel)
            .where(
                LearningAuditLogModel.tenant_id == self.tenant_id,
                LearningAuditLogModel.action.in_([
                    "ranking_produced",
                    "scored_action",
                    "exploration_decision",
                    "bandit_selection",
                ]),
                LearningAuditLogModel.created_at >= cutoff,
            )
            .order_by(LearningAuditLogModel.created_at.desc())
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        entries = list(result.scalars().all())

        return {
            "decisions": [self._audit_to_dict(e) for e in entries],
            "total": len(entries),
        }

    # ── Serializers ──────────────────────────────────────────────

    @staticmethod
    def _pattern_to_dict(p: TenantPatternModel) -> dict:
        return {
            "id": str(p.id),
            "pattern_type": p.pattern_type,
            "subject_feature": p.subject_feature,
            "subject_value": p.subject_value,
            "title": p.title,
            "explanation": p.explanation,
            "confidence": round(p.confidence, 4),
            "direction": p.direction,
            "supporting_observation_count": p.supporting_observation_count,
            "decay_weighted_reward": (
                round(p.decay_weighted_reward, 4) if p.decay_weighted_reward else None
            ),
            "evidence": p.evidence,
            "is_pinned": p.is_pinned,
            "source": p.source,
            "version": p.version,
            "observation_window_start": (
                p.observation_window_start.isoformat() if p.observation_window_start else None
            ),
            "observation_window_end": (
                p.observation_window_end.isoformat() if p.observation_window_end else None
            ),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }

    @staticmethod
    def _global_prior_to_dict(p: GlobalPatternStatModel) -> dict:
        return {
            "id": str(p.id),
            "feature_name": p.feature_name,
            "feature_value": p.feature_value,
            "category": p.category,
            "mean_reward": round(p.mean_reward, 4),
            "std_dev": round(p.std_dev, 4) if p.std_dev else None,
            "confidence_lower": round(p.confidence_lower, 4) if p.confidence_lower else None,
            "confidence_upper": round(p.confidence_upper, 4) if p.confidence_upper else None,
            "observation_count": p.observation_count,
            "tenant_count": p.tenant_count,
            "k_anonymity_threshold": p.k_anonymity_threshold,
            "version": p.version,
            "computed_at": p.computed_at.isoformat() if p.computed_at else None,
        }

    @staticmethod
    def _feature_vector_to_dict(fv: PostFeatureVectorModel) -> dict:
        return {
            "id": str(fv.id),
            "post_id": str(fv.post_id),
            "artist_id": str(fv.artist_id) if fv.artist_id else None,
            "campaign_id": str(fv.campaign_id) if fv.campaign_id else None,
            "features": fv.features,
            "platform": fv.platform,
            "content_type": fv.content_type,
            "post_hour": fv.post_hour,
            "post_day_of_week": fv.post_day_of_week,
            "extracted_at": fv.extracted_at.isoformat() if fv.extracted_at else None,
        }

    @staticmethod
    def _outcome_to_dict(o: PostOutcomeModel) -> dict:
        return {
            "id": str(o.id),
            "post_id": str(o.post_id),
            "measurement_window": o.measurement_window,
            "impressions": o.impressions,
            "reach": o.reach,
            "engagements": o.engagements,
            "engagement_rate": round(o.engagement_rate, 4) if o.engagement_rate else None,
            "reward": round(o.reward, 4) if o.reward else None,
            "reward_breakdown": o.reward_breakdown,
            "measured_at": o.measured_at.isoformat() if o.measured_at else None,
        }

    @staticmethod
    def _event_to_dict(e: LearningEventModel) -> dict:
        return {
            "id": str(e.id),
            "action_type": e.action_type,
            "features": e.features,
            "outcomes": e.outcomes,
            "reward": round(e.reward, 4) if e.reward else None,
            "observed_at": e.observed_at.isoformat() if e.observed_at else None,
            "source": e.source,
            "confidence": e.confidence,
        }

    @staticmethod
    def _audit_to_dict(a: LearningAuditLogModel) -> dict:
        return {
            "id": str(a.id),
            "action": a.action,
            "entity_type": a.entity_type,
            "entity_id": str(a.entity_id) if a.entity_id else None,
            "user_id": str(a.user_id) if a.user_id else None,
            "details": a.details,
            "explanation": a.explanation,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }

    @staticmethod
    def _is_suspicious(entry: dict) -> bool:
        """Heuristic for flagging suspicious audit entries."""
        details = entry.get("details", {})
        # Low confidence
        if details.get("confidence", 1.0) < 0.3:
            return True
        # Very low sample size
        if details.get("sample_size", 100) < 3:
            return True
        # Anomalous reward
        reward = details.get("reward")
        if reward is not None and (reward > 1.0 or reward < 0.0):
            return True
        return False
