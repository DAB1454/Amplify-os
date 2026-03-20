"""Smoke tests — verify every package installs and its public API imports.

Run with:
    python -m pytest tests/test_package_imports.py -v
"""

from __future__ import annotations


# ── amplify.core ─────────────────────────────────────────────────────────────

class TestCoreImports:
    def test_domain_models(self):
        from amplify.core.domain import (
            Artist, ArtistCreate,
            Campaign, CampaignCreate, CampaignStatus, CampaignPhase,
            Post, PostCreate, PostStatus, Platform,
            Release, ReleaseCreate, ReleaseType,
            Track, TrackCreate,
            Tenant, TenantCreate,
            Asset, AssetCreate, AssetType,
            Approval, ApprovalCreate,
            Experiment, ExperimentCreate,
            MetricEvent, MetricEventCreate,
        )

    def test_policies(self):
        from amplify.core.policies import (
            PolicyEngine, PolicyResult, PolicyVerdict,
            AntiSpamPolicy, BrandSafetyPolicy,
            IntegrityPolicy,
        )

    def test_policy_rules(self):
        from amplify.core.policies.rules import (
            BurstLimitRule, DuplicateCaptionRule, FrequencyLimitRule,
            HoursRestrictionRule, MissingLinkRule,
        )

    def test_workflows(self):
        from amplify.core.workflows.pre_release import PreReleaseWorkflow
        from amplify.core.workflows.release_day import ReleaseDayWorkflow
        from amplify.core.workflows.post_release_30 import PostRelease30Workflow
        from amplify.core.workflows.evergreen import EvergreenWorkflow

    def test_analytics(self):
        from amplify.core.analytics import PostScore, score_post, weekly_analyst_report

    def test_notifications(self):
        from amplify.core.notifications import (
            NotificationDispatcher, NotificationPayload, create_default_dispatcher,
        )


# ── amplify.db ───────────────────────────────────────────────────────────────

class TestDbImports:
    def test_base(self):
        from amplify.db.base import Base
        assert Base is not None

    def test_session(self):
        from amplify.db.session import create_engine, create_session_factory

    def test_models(self):
        from amplify.db.models import (
            TenantModel, UserModel, ArtistModel, ReleaseModel,
            TrackModel, CampaignModel, PostModel, ApprovalModel,
            AssetModel, ExperimentModel, ChannelConnectionModel,
        )

    def test_repository(self):
        from amplify.db.repository import BaseRepository


# ── amplify.adapters ─────────────────────────────────────────────────────────

class TestAdaptersImports:
    def test_base(self):
        from amplify.adapters import (
            BaseAdapter, PlatformAdapter,
            PublishResult, ConnectionHealth,
        )

    def test_token_store(self):
        from amplify.adapters.token_store import TokenSet, TokenStore

    def test_platform_modules(self):
        from amplify.adapters.instagram import adapter as _
        from amplify.adapters.tiktok import adapter as _
        from amplify.adapters.youtube import adapter as _
        from amplify.adapters.bandcamp import adapter as _
        from amplify.adapters.linktree import adapter as _


# ── amplify.agents ───────────────────────────────────────────────────────────

class TestAgentsImports:
    def test_runtime(self):
        from amplify.agents import (
            AgentConfig, ClaudeClient, ToolRegistry, AgentMemory,
            AgentRunner, AgentContext,
        )

    def test_subagents(self):
        from amplify.agents.subagents import (
            PlannerAgent, ContentAgent, PublisherAgent,
            CommunityAgent, AnalystAgent,
        )


# ── amplify.media ────────────────────────────────────────────────────────────

class TestMediaImports:
    def test_models(self):
        from amplify.media.models import RenderSpec, RenderedAsset, RenderType

    def test_cache(self):
        from amplify.media.cache import RenderCache

    def test_renderers(self):
        from amplify.media.renderers import (
            CaptionBurner, WaveformRenderer, LyricCardRenderer,
            HookVariantGenerator, TeaserRenderer,
        )

    def test_pipeline(self):
        from amplify.media.render_pipeline import RenderPipeline


# ── amplify.billing ──────────────────────────────────────────────────────────

class TestBillingImports:
    def test_plans(self):
        from amplify.billing.plans import PlanTier, get_plan, DEFAULT_PLANS

    def test_metering(self):
        from amplify.billing.metering import MeteringService

    def test_enforcement(self):
        from amplify.billing.enforcement import SubscriptionEnforcer, EnforcementResult

    def test_invoices(self):
        from amplify.billing.invoices import InvoiceService

    def test_stripe(self):
        from amplify.billing.stripe_client import StubPaymentProvider


# ── amplify.observability ────────────────────────────────────────────────────

class TestObservabilityImports:
    def test_metrics(self):
        from amplify.observability.metrics import MetricsCollector

    def test_tracing(self):
        from amplify.observability.tracing import trace, SpanContext

    def test_alerts(self):
        from amplify.observability.alerts import AlertManager

    def test_logging(self):
        from amplify.observability.logging import setup_logging, get_logger


# ── Cross-package namespace ──────────────────────────────────────────────────

class TestNamespaceIntegrity:
    """Verify the amplify namespace merges correctly across all packages."""

    def test_amplify_namespace_not_a_regular_package(self):
        """amplify/ must be an implicit namespace package (no __init__.py)."""
        import amplify
        # Namespace packages have no __file__ attribute or it's None
        assert not hasattr(amplify, "__file__") or amplify.__file__ is None

    def test_all_subpackages_accessible(self):
        import amplify.core
        import amplify.db
        import amplify.adapters
        import amplify.agents
        import amplify.media
        import amplify.billing
        import amplify.observability
