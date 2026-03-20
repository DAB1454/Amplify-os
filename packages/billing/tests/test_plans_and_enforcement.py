"""Tests for plan tiers, metering, enforcement, and Stripe abstraction."""

from __future__ import annotations

import pytest

from amplify.billing.plans import (
    ALL_TIERS,
    BillingPlan,
    DEFAULT_PLANS,
    PlanLimits,
    get_plan,
    get_plan_limits,
)
from amplify.billing.metering import (
    ALL_METRICS,
    METRIC_AI_GENERATIONS,
    METRIC_MEDIA_RENDERS,
    METRIC_POSTS_SCHEDULED,
    LimitExceeded,
    MeteringService,
)
from amplify.billing.stripe_client import (
    StubPaymentProvider,
    SubscriptionInfo,
    SubscriptionStatus,
)


# ── Plan definitions ─────────────────────────────────────────────


class TestPlanTiers:
    def test_three_tiers(self):
        assert ALL_TIERS == ["solo", "pro", "agency"]

    def test_three_plans_defined(self):
        assert len(DEFAULT_PLANS) == 3

    def test_solo_is_free(self):
        solo = get_plan("solo")
        assert solo is not None
        assert solo.price_monthly == 0
        assert solo.price_yearly == 0

    def test_pro_pricing(self):
        pro = get_plan("pro")
        assert pro is not None
        assert pro.price_monthly == 29.00
        assert pro.price_yearly == 290.00

    def test_agency_pricing(self):
        agency = get_plan("agency")
        assert agency is not None
        assert agency.price_monthly == 99.00

    def test_unknown_plan_returns_none(self):
        assert get_plan("enterprise") is None

    def test_get_plan_limits_defaults_to_solo(self):
        limits = get_plan_limits("nonexistent")
        solo_limits = get_plan_limits("solo")
        assert limits.artists == solo_limits.artists


class TestPlanLimits:
    def test_solo_limits(self):
        limits = get_plan_limits("solo")
        assert limits.artists == 1
        assert limits.channels == 3
        assert limits.scheduled_posts_per_month == 10
        assert limits.active_campaigns == 2
        assert limits.media_renders_per_month == 5
        assert limits.ai_generations_per_month == 0
        assert limits.team_members == 1
        assert limits.api_access is False

    def test_pro_limits(self):
        limits = get_plan_limits("pro")
        assert limits.artists == 5
        assert limits.channels == -1  # unlimited
        assert limits.scheduled_posts_per_month == 200
        assert limits.active_campaigns == 10
        assert limits.team_members == 3

    def test_agency_all_unlimited(self):
        limits = get_plan_limits("agency")
        assert limits.artists == -1
        assert limits.channels == -1
        assert limits.scheduled_posts_per_month == -1
        assert limits.active_campaigns == -1
        assert limits.media_renders_per_month == -1
        assert limits.ai_generations_per_month == -1
        assert limits.team_members == -1
        assert limits.api_access is True

    def test_each_plan_has_features(self):
        for plan in DEFAULT_PLANS:
            assert len(plan.features) > 0

    def test_plans_have_unique_tiers(self):
        tiers = [p.tier for p in DEFAULT_PLANS]
        assert len(tiers) == len(set(tiers))


# ── Metering ─────────────────────────────────────────────────────


class TestMetering:
    @pytest.fixture
    def metering(self):
        return MeteringService()

    @pytest.mark.asyncio
    async def test_record_and_get_usage(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 3)
        usage = await metering.get_usage("t1", METRIC_POSTS_SCHEDULED)
        assert usage == 3

    @pytest.mark.asyncio
    async def test_usage_accumulates(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 2)
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 5)
        usage = await metering.get_usage("t1", METRIC_POSTS_SCHEDULED)
        assert usage == 7

    @pytest.mark.asyncio
    async def test_usage_isolated_by_tenant(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 10)
        await metering.record_usage("t2", METRIC_POSTS_SCHEDULED, 3)
        assert await metering.get_usage("t1", METRIC_POSTS_SCHEDULED) == 10
        assert await metering.get_usage("t2", METRIC_POSTS_SCHEDULED) == 3

    @pytest.mark.asyncio
    async def test_usage_isolated_by_metric(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 5)
        await metering.record_usage("t1", METRIC_MEDIA_RENDERS, 2)
        assert await metering.get_usage("t1", METRIC_POSTS_SCHEDULED) == 5
        assert await metering.get_usage("t1", METRIC_MEDIA_RENDERS) == 2

    @pytest.mark.asyncio
    async def test_check_limit_within(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 5)
        assert await metering.check_limit("t1", METRIC_POSTS_SCHEDULED, "solo") is True

    @pytest.mark.asyncio
    async def test_check_limit_exceeded(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 10)
        assert await metering.check_limit("t1", METRIC_POSTS_SCHEDULED, "solo") is False

    @pytest.mark.asyncio
    async def test_check_limit_unlimited(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 999999)
        assert await metering.check_limit("t1", METRIC_POSTS_SCHEDULED, "agency") is True

    @pytest.mark.asyncio
    async def test_enforce_limit_raises(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 10)
        with pytest.raises(LimitExceeded) as exc:
            await metering.enforce_limit("t1", METRIC_POSTS_SCHEDULED, "solo")
        assert exc.value.metric == METRIC_POSTS_SCHEDULED
        assert exc.value.limit == 10
        assert "solo" in exc.value.tier

    @pytest.mark.asyncio
    async def test_enforce_limit_ok(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 5)
        await metering.enforce_limit("t1", METRIC_POSTS_SCHEDULED, "solo")  # no raise

    @pytest.mark.asyncio
    async def test_get_all_usage(self, metering):
        await metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 5)
        await metering.record_usage("t1", METRIC_MEDIA_RENDERS, 2)
        all_usage = await metering.get_all_usage("t1")
        assert all_usage[METRIC_POSTS_SCHEDULED] == 5
        assert all_usage[METRIC_MEDIA_RENDERS] == 2
        assert all_usage[METRIC_AI_GENERATIONS] == 0

    def test_reset_usage(self, metering):
        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(metering.record_usage("t1", METRIC_POSTS_SCHEDULED, 10))
        metering.reset_usage("t1")
        result = loop.run_until_complete(metering.get_usage("t1", METRIC_POSTS_SCHEDULED))
        assert result == 0
        loop.close()


# ── Stub payment provider ───────────────────────────────────────


class TestStubPaymentProvider:
    @pytest.fixture
    def provider(self):
        return StubPaymentProvider()

    @pytest.mark.asyncio
    async def test_create_customer(self, provider):
        customer = await provider.create_customer(
            email="test@example.com",
            name="Test Artist",
            tenant_id="t1",
        )
        assert customer.customer_id.startswith("cus_stub_")
        assert customer.email == "test@example.com"
        assert customer.metadata["tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_create_checkout_session(self, provider):
        session = await provider.create_checkout_session(
            customer_id="cus_123",
            price_id="pro",
            success_url="http://localhost/success",
            cancel_url="http://localhost/cancel",
        )
        assert session.session_id.startswith("cs_stub_")
        assert "stub.checkout" in session.url

    @pytest.mark.asyncio
    async def test_create_portal_session(self, provider):
        portal = await provider.create_billing_portal_session(
            customer_id="cus_123",
            return_url="http://localhost/billing",
        )
        assert "stub.billing" in portal.url

    @pytest.mark.asyncio
    async def test_get_subscription(self, provider):
        from datetime import datetime
        sub = SubscriptionInfo(
            subscription_id="sub_1",
            customer_id="cus_1",
            plan_tier="pro",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=datetime(2026, 1, 1),
            current_period_end=datetime(2026, 2, 1),
        )
        provider.add_subscription(sub)
        result = await provider.get_subscription("sub_1")
        assert result.plan_tier == "pro"
        assert result.status == SubscriptionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_cancel_subscription(self, provider):
        from datetime import datetime
        sub = SubscriptionInfo(
            subscription_id="sub_2",
            customer_id="cus_1",
            plan_tier="agency",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=datetime(2026, 1, 1),
            current_period_end=datetime(2026, 2, 1),
        )
        provider.add_subscription(sub)
        result = await provider.cancel_subscription("sub_2", at_period_end=True)
        assert result.cancel_at_period_end is True
        assert result.status == SubscriptionStatus.ACTIVE  # still active until period end

    @pytest.mark.asyncio
    async def test_cancel_subscription_immediate(self, provider):
        from datetime import datetime
        sub = SubscriptionInfo(
            subscription_id="sub_3",
            customer_id="cus_1",
            plan_tier="pro",
            status=SubscriptionStatus.ACTIVE,
            current_period_start=datetime(2026, 1, 1),
            current_period_end=datetime(2026, 2, 1),
        )
        provider.add_subscription(sub)
        result = await provider.cancel_subscription("sub_3", at_period_end=False)
        assert result.status == SubscriptionStatus.CANCELED

    @pytest.mark.asyncio
    async def test_get_nonexistent_subscription(self, provider):
        with pytest.raises(ValueError, match="not found"):
            await provider.get_subscription("sub_nonexistent")

    @pytest.mark.asyncio
    async def test_webhook_handler(self, provider):
        result = await provider.handle_webhook(b"payload", "sig")
        assert result["type"] == "stub.event"


# ── Tenant isolation ─────────────────────────────────────────────


class TestTenantIsolation:
    """Verify that metering and limits are strictly tenant-scoped."""

    @pytest.mark.asyncio
    async def test_metering_cross_tenant_isolation(self):
        """Tenant A's usage must never leak into tenant B's counters."""
        metering = MeteringService()
        await metering.record_usage("tenant_a", METRIC_POSTS_SCHEDULED, 50)
        await metering.record_usage("tenant_b", METRIC_POSTS_SCHEDULED, 2)

        assert await metering.get_usage("tenant_a", METRIC_POSTS_SCHEDULED) == 50
        assert await metering.get_usage("tenant_b", METRIC_POSTS_SCHEDULED) == 2

        # Tenant B is within solo limits, tenant A is not
        assert await metering.check_limit("tenant_b", METRIC_POSTS_SCHEDULED, "solo") is True
        assert await metering.check_limit("tenant_a", METRIC_POSTS_SCHEDULED, "solo") is False

    @pytest.mark.asyncio
    async def test_limit_enforcement_per_tier(self):
        """Same usage, different tiers → different enforcement."""
        metering = MeteringService()
        await metering.record_usage("t_solo", METRIC_POSTS_SCHEDULED, 10)
        await metering.record_usage("t_pro", METRIC_POSTS_SCHEDULED, 10)
        await metering.record_usage("t_agency", METRIC_POSTS_SCHEDULED, 10)

        # Solo limit is 10, so at limit
        assert await metering.check_limit("t_solo", METRIC_POSTS_SCHEDULED, "solo") is False
        # Pro limit is 200, so within
        assert await metering.check_limit("t_pro", METRIC_POSTS_SCHEDULED, "pro") is True
        # Agency is unlimited
        assert await metering.check_limit("t_agency", METRIC_POSTS_SCHEDULED, "agency") is True

    @pytest.mark.asyncio
    async def test_render_limit_enforcement(self):
        """Solo gets 5 renders, pro gets 100."""
        metering = MeteringService()
        await metering.record_usage("t1", METRIC_MEDIA_RENDERS, 5)
        assert await metering.check_limit("t1", METRIC_MEDIA_RENDERS, "solo") is False
        assert await metering.check_limit("t1", METRIC_MEDIA_RENDERS, "pro") is True

    @pytest.mark.asyncio
    async def test_ai_gen_blocked_on_solo(self):
        """Solo plan has 0 AI generations — even 1 usage should fail."""
        metering = MeteringService()
        # Don't record any usage — limit is 0, so even starting is blocked
        assert await metering.check_limit("t1", METRIC_AI_GENERATIONS, "solo") is False

    @pytest.mark.asyncio
    async def test_all_usage_returns_all_metrics(self):
        metering = MeteringService()
        all_usage = await metering.get_all_usage("empty_tenant")
        assert set(all_usage.keys()) == set(ALL_METRICS)
        assert all(v == 0 for v in all_usage.values())
