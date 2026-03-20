"""Tests for the approval rules engine."""

from __future__ import annotations

import pytest

from amplify.core.domain.approval import (
    ActionType,
    ApprovalRulesEngine,
    BulkOperationRule,
    CampaignLaunchRule,
    DestructiveActionRule,
    HighSpendRule,
    NewChannelRule,
    PolicyEscalationRule,
    RiskLevel,
    create_default_approval_engine,
)


class TestHighSpendRule:
    def test_below_threshold_no_approval(self):
        rule = HighSpendRule(threshold=500)
        result = rule.evaluate(ActionType.BUDGET_CHANGE, {"amount": 100})
        assert not result.requires_approval

    def test_at_threshold_requires_approval(self):
        rule = HighSpendRule(threshold=500)
        result = rule.evaluate(ActionType.BUDGET_CHANGE, {"amount": 500})
        assert result.requires_approval
        assert result.risk_level == RiskLevel.HIGH

    def test_above_threshold_requires_approval(self):
        rule = HighSpendRule(threshold=500)
        result = rule.evaluate(ActionType.BUDGET_CHANGE, {"amount": 1000})
        assert result.requires_approval
        assert any("$1000.00" in r for r in result.reasons)

    def test_ignores_other_action_types(self):
        rule = HighSpendRule(threshold=500)
        result = rule.evaluate(ActionType.PUBLISH_POST, {"amount": 9999})
        assert not result.requires_approval

    def test_custom_threshold(self):
        rule = HighSpendRule(threshold=100)
        result = rule.evaluate(ActionType.BUDGET_CHANGE, {"amount": 150})
        assert result.requires_approval


class TestBulkOperationRule:
    def test_below_threshold(self):
        rule = BulkOperationRule(threshold=10)
        result = rule.evaluate(ActionType.BULK_OPERATION, {"affected_count": 5})
        assert not result.requires_approval

    def test_above_threshold(self):
        rule = BulkOperationRule(threshold=10)
        result = rule.evaluate(ActionType.BULK_OPERATION, {"affected_count": 15})
        assert result.requires_approval
        assert result.risk_level == RiskLevel.HIGH

    def test_ignores_other_actions(self):
        rule = BulkOperationRule(threshold=10)
        result = rule.evaluate(ActionType.PUBLISH_POST, {"affected_count": 100})
        assert not result.requires_approval


class TestNewChannelRule:
    def test_requires_approval(self):
        rule = NewChannelRule()
        result = rule.evaluate(ActionType.CHANNEL_CONNECT, {})
        assert result.requires_approval
        assert result.risk_level == RiskLevel.MEDIUM

    def test_ignores_other_actions(self):
        rule = NewChannelRule()
        result = rule.evaluate(ActionType.PUBLISH_POST, {})
        assert not result.requires_approval


class TestCampaignLaunchRule:
    def test_requires_approval(self):
        rule = CampaignLaunchRule()
        result = rule.evaluate(ActionType.CAMPAIGN_LAUNCH, {})
        assert result.requires_approval
        assert result.risk_level == RiskLevel.MEDIUM

    def test_ignores_other_actions(self):
        rule = CampaignLaunchRule()
        result = rule.evaluate(ActionType.PUBLISH_POST, {})
        assert not result.requires_approval


class TestDestructiveActionRule:
    def test_published_post_deletion_requires_approval(self):
        rule = DestructiveActionRule()
        result = rule.evaluate(ActionType.DELETE_POST, {"status": "published"})
        assert result.requires_approval
        assert result.risk_level == RiskLevel.CRITICAL

    def test_draft_post_deletion_allowed(self):
        rule = DestructiveActionRule()
        result = rule.evaluate(ActionType.DELETE_POST, {"status": "draft"})
        assert not result.requires_approval

    def test_ignores_other_actions(self):
        rule = DestructiveActionRule()
        result = rule.evaluate(ActionType.PUBLISH_POST, {"status": "published"})
        assert not result.requires_approval


class TestPolicyEscalationRule:
    def test_escalates_require_approval(self):
        rule = PolicyEscalationRule()
        result = rule.evaluate(
            ActionType.PUBLISH_POST,
            {"policy_decision": "require_approval", "policy_reasons": ["Missing link"]},
        )
        assert result.requires_approval
        assert "Missing link" in result.reasons

    def test_does_not_escalate_allow(self):
        rule = PolicyEscalationRule()
        result = rule.evaluate(ActionType.PUBLISH_POST, {"policy_decision": "allow"})
        assert not result.requires_approval

    def test_does_not_escalate_block(self):
        rule = PolicyEscalationRule()
        result = rule.evaluate(ActionType.PUBLISH_POST, {"policy_decision": "block"})
        assert not result.requires_approval


class TestApprovalRulesEngine:
    def test_empty_engine(self):
        engine = ApprovalRulesEngine()
        result = engine.evaluate(ActionType.PUBLISH_POST, {})
        assert not result.requires_approval
        assert result.risk_level == RiskLevel.LOW

    def test_multiple_rules_aggregate(self):
        engine = ApprovalRulesEngine()
        engine.add_rule(HighSpendRule(threshold=100))
        engine.add_rule(BulkOperationRule(threshold=5))

        # Trigger both
        result = engine.evaluate(
            ActionType.BUDGET_CHANGE, {"amount": 200}
        )
        assert result.requires_approval
        assert result.risk_level == RiskLevel.HIGH

    def test_highest_risk_wins(self):
        engine = ApprovalRulesEngine()
        engine.add_rule(NewChannelRule())  # MEDIUM
        engine.add_rule(DestructiveActionRule())  # CRITICAL (for published)

        result = engine.evaluate(ActionType.DELETE_POST, {"status": "published"})
        assert result.risk_level == RiskLevel.CRITICAL

    def test_reasons_accumulate(self):
        engine = ApprovalRulesEngine()
        engine.add_rule(PolicyEscalationRule())
        engine.add_rule(NewChannelRule())

        result = engine.evaluate(
            ActionType.CHANNEL_CONNECT,
            {"policy_decision": "require_approval", "policy_reasons": ["Reason A"]},
        )
        assert len(result.reasons) >= 2


class TestDefaultEngine:
    def test_factory_creates_engine_with_rules(self):
        engine = create_default_approval_engine()
        assert len(engine.rules) == 6

    def test_safe_publish_does_not_need_approval(self):
        engine = create_default_approval_engine()
        result = engine.evaluate(ActionType.PUBLISH_POST, {})
        assert not result.requires_approval

    def test_big_budget_needs_approval(self):
        engine = create_default_approval_engine()
        result = engine.evaluate(ActionType.BUDGET_CHANGE, {"amount": 1000})
        assert result.requires_approval

    def test_channel_connect_needs_approval(self):
        engine = create_default_approval_engine()
        result = engine.evaluate(ActionType.CHANNEL_CONNECT, {})
        assert result.requires_approval

    def test_campaign_launch_needs_approval(self):
        engine = create_default_approval_engine()
        result = engine.evaluate(ActionType.CAMPAIGN_LAUNCH, {})
        assert result.requires_approval

    def test_delete_published_needs_approval(self):
        engine = create_default_approval_engine()
        result = engine.evaluate(ActionType.DELETE_POST, {"status": "published"})
        assert result.requires_approval
        assert result.risk_level == RiskLevel.CRITICAL

    def test_custom_thresholds(self):
        engine = create_default_approval_engine(spend_threshold=50, bulk_threshold=3)
        result = engine.evaluate(ActionType.BUDGET_CHANGE, {"amount": 60})
        assert result.requires_approval
