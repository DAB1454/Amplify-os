"""All SQLAlchemy ORM models — import here so Alembic can discover them."""

from amplify.db.models.tenant import TenantModel
from amplify.db.models.user import UserModel
from amplify.db.models.membership import MembershipModel
from amplify.db.models.artist import ArtistModel
from amplify.db.models.release import ReleaseModel
from amplify.db.models.track import TrackModel
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.campaign_template import CampaignTemplateModel
from amplify.db.models.calendar_item import CalendarItemModel
from amplify.db.models.asset import AssetModel, AssetVariantModel
from amplify.db.models.post import PostModel
from amplify.db.models.approval import ApprovalModel, ApprovalCommentModel
from amplify.db.models.experiment import ExperimentModel
from amplify.db.models.metric import MetricEventModel, DailyMetricModel
from amplify.db.models.audit_log import AuditLogModel
from amplify.db.models.automation_audit import AutomationAuditModel
from amplify.db.models.notification import NotificationModel
from amplify.db.models.billing import BillingPlanModel, SubscriptionModel
from amplify.db.models.assisted_task import AssistedTaskModel
from amplify.db.models.redirect import RedirectModel
from amplify.db.models.learning import (
    LearningEventModel,
    PostFeatureVectorModel,
    PostOutcomeModel,
    TenantPatternModel,
    GlobalPatternStatModel,
    CohortDefinitionModel,
    PromptVersionModel,
    PromptAssignmentModel,
    EvaluationRunModel,
    EvaluationResultModel,
    LearningAuditLogModel,
)

__all__ = [
    "TenantModel",
    "UserModel",
    "MembershipModel",
    "ArtistModel",
    "ReleaseModel",
    "TrackModel",
    "ChannelConnectionModel",
    "CampaignModel",
    "CampaignTemplateModel",
    "CalendarItemModel",
    "AssetModel",
    "AssetVariantModel",
    "PostModel",
    "ApprovalModel",
    "ApprovalCommentModel",
    "ExperimentModel",
    "MetricEventModel",
    "DailyMetricModel",
    "AuditLogModel",
    "AutomationAuditModel",
    "NotificationModel",
    "BillingPlanModel",
    "SubscriptionModel",
    "AssistedTaskModel",
    "RedirectModel",
    "LearningEventModel",
    "PostFeatureVectorModel",
    "PostOutcomeModel",
    "TenantPatternModel",
    "GlobalPatternStatModel",
    "CohortDefinitionModel",
    "PromptVersionModel",
    "PromptAssignmentModel",
    "EvaluationRunModel",
    "EvaluationResultModel",
    "LearningAuditLogModel",
]
