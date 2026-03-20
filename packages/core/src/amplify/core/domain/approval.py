"""Approval domain models and rules engine for high-risk action review."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ── Status ────────────────────────────────────────────────────────


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION_REQUESTED = "revision_requested"
    RESUBMITTED = "resubmitted"
    EXPIRED = "expired"


# ── Risk classification ──────────────────────────────────────────


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionType(str, Enum):
    """Action types that can require approval."""
    PUBLISH_POST = "publish_post"
    MODERATE_REPLY = "moderate_reply"
    DELETE_POST = "delete_post"
    CHANNEL_CONNECT = "channel_connect"
    BUDGET_CHANGE = "budget_change"
    CAMPAIGN_LAUNCH = "campaign_launch"
    BULK_OPERATION = "bulk_operation"
    ASSET_PUBLISH = "asset_publish"


# ── Approval request ─────────────────────────────────────────────


class Approval(BaseModel):
    """An approval request for a high-risk action."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    action_type: ActionType
    entity_type: str = ""  # post, asset, campaign, channel
    entity_id: UUID | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    risk_reasons: list[str] = Field(default_factory=list)
    policy_snapshot: dict = Field(default_factory=dict)

    # People
    requested_by: UUID | None = None
    reviewed_by: UUID | None = None

    # State
    status: ApprovalStatus = ApprovalStatus.PENDING
    notes: str = ""

    # Payload — the original action data, replayed on approve
    action_payload: dict = Field(default_factory=dict)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: datetime | None = None
    expires_at: datetime | None = None


class ApprovalComment(BaseModel):
    """A reviewer comment on an approval request."""

    id: UUID = Field(default_factory=uuid4)
    approval_id: UUID
    user_id: UUID
    body: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Request/update schemas ───────────────────────────────────────


class ApprovalCreate(BaseModel):
    """Schema for creating a new approval request."""
    action_type: ActionType
    entity_type: str = ""
    entity_id: UUID | None = None
    notes: str = ""
    action_payload: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    """Schema for approve/reject/revision-request."""
    comment: str = ""


class ApprovalResubmit(BaseModel):
    """Schema for resubmitting after revision request."""
    notes: str = ""
    action_payload: dict | None = None


class CommentCreate(BaseModel):
    """Schema for adding a reviewer comment."""
    body: str = Field(min_length=1, max_length=2000)


# ── Approval rules engine ────────────────────────────────────────


class ApprovalRuleResult(BaseModel):
    """Result from a single approval rule evaluation."""
    requires_approval: bool = False
    risk_level: RiskLevel = RiskLevel.LOW
    reasons: list[str] = Field(default_factory=list)


class ApprovalRule:
    """Base class for approval rules."""

    name: str = "base_rule"

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        return ApprovalRuleResult()


class HighSpendRule(ApprovalRule):
    """Flag budget changes above a threshold."""

    name = "high_spend"

    def __init__(self, threshold: float = 500.0) -> None:
        self.threshold = threshold

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        if action_type != ActionType.BUDGET_CHANGE:
            return ApprovalRuleResult()
        amount = payload.get("amount", 0)
        if amount >= self.threshold:
            return ApprovalRuleResult(
                requires_approval=True,
                risk_level=RiskLevel.HIGH,
                reasons=[f"Budget change ${amount:.2f} exceeds ${self.threshold:.2f} threshold"],
            )
        return ApprovalRuleResult()


class BulkOperationRule(ApprovalRule):
    """Flag bulk operations affecting many entities."""

    name = "bulk_operation"

    def __init__(self, threshold: int = 10) -> None:
        self.threshold = threshold

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        if action_type != ActionType.BULK_OPERATION:
            return ApprovalRuleResult()
        count = payload.get("affected_count", 0)
        if count >= self.threshold:
            return ApprovalRuleResult(
                requires_approval=True,
                risk_level=RiskLevel.HIGH,
                reasons=[f"Bulk operation affects {count} entities (threshold: {self.threshold})"],
            )
        return ApprovalRuleResult()


class NewChannelRule(ApprovalRule):
    """Require approval for connecting new platform channels."""

    name = "new_channel"

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        if action_type != ActionType.CHANNEL_CONNECT:
            return ApprovalRuleResult()
        return ApprovalRuleResult(
            requires_approval=True,
            risk_level=RiskLevel.MEDIUM,
            reasons=["New platform channel connections require approval"],
        )


class CampaignLaunchRule(ApprovalRule):
    """Require approval for launching campaigns."""

    name = "campaign_launch"

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        if action_type != ActionType.CAMPAIGN_LAUNCH:
            return ApprovalRuleResult()
        return ApprovalRuleResult(
            requires_approval=True,
            risk_level=RiskLevel.MEDIUM,
            reasons=["Campaign launches require review before activation"],
        )


class DestructiveActionRule(ApprovalRule):
    """Flag destructive actions like deleting published posts."""

    name = "destructive_action"

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        if action_type != ActionType.DELETE_POST:
            return ApprovalRuleResult()
        if payload.get("status") == "published":
            return ApprovalRuleResult(
                requires_approval=True,
                risk_level=RiskLevel.CRITICAL,
                reasons=["Deleting a published post is a destructive action"],
            )
        return ApprovalRuleResult()


class PolicyEscalationRule(ApprovalRule):
    """Escalate actions that the policy engine flagged as REQUIRE_APPROVAL."""

    name = "policy_escalation"

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        policy_decision = payload.get("policy_decision", "")
        if policy_decision == "require_approval":
            reasons = payload.get("policy_reasons", ["Policy engine flagged for review"])
            return ApprovalRuleResult(
                requires_approval=True,
                risk_level=RiskLevel.MEDIUM,
                reasons=reasons,
            )
        return ApprovalRuleResult()


# ── Rules engine ─────────────────────────────────────────────────


class ApprovalRulesEngine:
    """Evaluates whether an action requires manual approval."""

    def __init__(self) -> None:
        self._rules: list[ApprovalRule] = []

    def add_rule(self, rule: ApprovalRule) -> None:
        self._rules.append(rule)

    @property
    def rules(self) -> list[ApprovalRule]:
        return list(self._rules)

    def evaluate(self, action_type: str, payload: dict) -> ApprovalRuleResult:
        """Run all rules.  Returns a merged result."""
        requires = False
        highest_risk = RiskLevel.LOW
        all_reasons: list[str] = []

        risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]

        for rule in self._rules:
            result = rule.evaluate(action_type, payload)
            if result.requires_approval:
                requires = True
                all_reasons.extend(result.reasons)
                if risk_order.index(result.risk_level) > risk_order.index(highest_risk):
                    highest_risk = result.risk_level

        return ApprovalRuleResult(
            requires_approval=requires,
            risk_level=highest_risk,
            reasons=all_reasons,
        )


def create_default_approval_engine(
    *,
    spend_threshold: float = 500.0,
    bulk_threshold: int = 10,
) -> ApprovalRulesEngine:
    """Create an ApprovalRulesEngine with all default rules."""
    engine = ApprovalRulesEngine()
    engine.add_rule(HighSpendRule(threshold=spend_threshold))
    engine.add_rule(BulkOperationRule(threshold=bulk_threshold))
    engine.add_rule(NewChannelRule())
    engine.add_rule(CampaignLaunchRule())
    engine.add_rule(DestructiveActionRule())
    engine.add_rule(PolicyEscalationRule())
    return engine
