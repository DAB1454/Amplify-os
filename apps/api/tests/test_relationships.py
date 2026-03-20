"""Tests for SQLAlchemy model relationships and constraints."""

from __future__ import annotations

import pytest

from amplify.db.models import (
    ApprovalModel,
    ArtistModel,
    AssetModel,
    AssetVariantModel,
    AuditLogModel,
    BillingPlanModel,
    CalendarItemModel,
    CampaignModel,
    ChannelConnectionModel,
    DailyMetricModel,
    ExperimentModel,
    MembershipModel,
    MetricEventModel,
    PostModel,
    ReleaseModel,
    SubscriptionModel,
    TenantModel,
    TrackModel,
    UserModel,
)


def _col_names(model) -> set[str]:
    """Return the set of column names for a model."""
    return {c.name for c in model.__table__.columns}


def _fk_targets(model) -> set[str]:
    """Return the set of foreign key target columns (e.g. 'tenants.id')."""
    return {
        list(fk.target_fullname for fk in col.foreign_keys).pop()
        for col in model.__table__.columns
        if col.foreign_keys
    }


# ---------------------------------------------------------------------------
# Table names
# ---------------------------------------------------------------------------


def test_tenant_model_has_correct_table_name():
    assert TenantModel.__tablename__ == "tenants"


def test_user_model_has_correct_table_name():
    assert UserModel.__tablename__ == "users"


def test_artist_model_has_correct_table_name():
    assert ArtistModel.__tablename__ == "artists"


def test_release_model_has_correct_table_name():
    assert ReleaseModel.__tablename__ == "releases"


def test_track_model_has_correct_table_name():
    assert TrackModel.__tablename__ == "tracks"


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------


def test_artist_model_has_tenant_id():
    cols = _col_names(ArtistModel)
    assert "tenant_id" in cols
    assert "tenants.id" in _fk_targets(ArtistModel)


def test_release_model_has_artist_fk():
    cols = _col_names(ReleaseModel)
    assert "artist_id" in cols
    fks = _fk_targets(ReleaseModel)
    assert "artists.id" in fks
    assert "tenants.id" in fks


def test_track_model_has_release_fk():
    cols = _col_names(TrackModel)
    assert "release_id" in cols
    assert "releases.id" in _fk_targets(TrackModel)


def test_campaign_model_has_artist_and_release_fks():
    cols = _col_names(CampaignModel)
    assert "artist_id" in cols
    assert "release_id" in cols
    fks = _fk_targets(CampaignModel)
    assert "artists.id" in fks
    assert "releases.id" in fks
    assert "tenants.id" in fks


def test_post_model_has_campaign_and_channel_fks():
    cols = _col_names(PostModel)
    assert "campaign_id" in cols
    assert "channel_id" in cols
    fks = _fk_targets(PostModel)
    assert "campaigns.id" in fks
    assert "channel_connections.id" in fks


def test_asset_model_has_release_and_campaign_fks():
    cols = _col_names(AssetModel)
    assert "release_id" in cols
    assert "campaign_id" in cols
    fks = _fk_targets(AssetModel)
    assert "releases.id" in fks
    assert "campaigns.id" in fks


def test_approval_model_has_post_and_asset_fks():
    cols = _col_names(ApprovalModel)
    assert "post_id" in cols
    assert "asset_id" in cols
    assert "requested_by" in cols
    assert "reviewed_by" in cols
    fks = _fk_targets(ApprovalModel)
    assert "posts.id" in fks
    assert "assets.id" in fks
    assert "users.id" in fks


def test_experiment_model_has_campaign_fk():
    cols = _col_names(ExperimentModel)
    assert "campaign_id" in cols
    assert "campaigns.id" in _fk_targets(ExperimentModel)


def test_membership_model_has_user_and_tenant_fks():
    cols = _col_names(MembershipModel)
    assert "user_id" in cols
    assert "tenant_id" in cols
    fks = _fk_targets(MembershipModel)
    assert "users.id" in fks
    assert "tenants.id" in fks


def test_subscription_model_has_tenant_and_plan_fks():
    cols = _col_names(SubscriptionModel)
    assert "tenant_id" in cols
    assert "plan_id" in cols
    fks = _fk_targets(SubscriptionModel)
    assert "tenants.id" in fks
    assert "billing_plans.id" in fks


# ---------------------------------------------------------------------------
# Column completeness checks
# ---------------------------------------------------------------------------


def test_metric_event_model_columns():
    cols = _col_names(MetricEventModel)
    expected = {
        "id", "tenant_id", "source", "event_type", "entity_type",
        "entity_id", "value", "metadata", "occurred_at",
        "created_at", "updated_at",
    }
    assert expected.issubset(cols)


def test_daily_metric_model_columns():
    cols = _col_names(DailyMetricModel)
    expected = {
        "id", "tenant_id", "source", "metric_name", "entity_type",
        "entity_id", "date", "value", "previous_value",
        "created_at", "updated_at",
    }
    assert expected.issubset(cols)


def test_audit_log_model_columns():
    cols = _col_names(AuditLogModel)
    expected = {
        "id", "tenant_id", "user_id", "action", "entity_type",
        "entity_id", "changes", "ip_address",
        "created_at", "updated_at",
    }
    assert expected.issubset(cols)


def test_channel_connection_model_columns():
    cols = _col_names(ChannelConnectionModel)
    expected = {
        "id", "tenant_id", "artist_id", "platform",
        "platform_account_id", "display_name",
        "access_token", "refresh_token", "token_expires_at",
        "is_active", "settings",
    }
    assert expected.issubset(cols)


def test_calendar_item_model_columns():
    cols = _col_names(CalendarItemModel)
    expected = {
        "id", "tenant_id", "campaign_id", "title", "description",
        "item_type", "scheduled_date", "scheduled_time", "is_completed",
    }
    assert expected.issubset(cols)


def test_billing_plan_model_columns():
    cols = _col_names(BillingPlanModel)
    expected = {
        "id", "name", "tier", "price_monthly", "price_yearly",
        "features", "limits", "is_active",
    }
    assert expected.issubset(cols)


def test_asset_variant_model_columns():
    cols = _col_names(AssetVariantModel)
    expected = {
        "id", "asset_id", "platform", "file_url",
        "width", "height", "duration_seconds", "format",
    }
    assert expected.issubset(cols)
    assert "assets.id" in _fk_targets(AssetVariantModel)
