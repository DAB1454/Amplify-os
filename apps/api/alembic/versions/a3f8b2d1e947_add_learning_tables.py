"""add learning tables

Revision ID: a3f8b2d1e947
Revises: 64ece5c29775
Create Date: 2026-03-20 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f8b2d1e947'
down_revision: Union[str, None] = '64ece5c29775'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── learning_events ──────────────────────────────────────────
    op.create_table('learning_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('action_id', sa.Uuid(), nullable=True),
        sa.Column('artist_id', sa.Uuid(), nullable=True),
        sa.Column('release_id', sa.Uuid(), nullable=True),
        sa.Column('campaign_id', sa.Uuid(), nullable=True),
        sa.Column('post_id', sa.Uuid(), nullable=True),
        sa.Column('asset_id', sa.Uuid(), nullable=True),
        sa.Column('platform', sa.String(length=50), nullable=True),
        sa.Column('features', sa.JSON(), nullable=False),
        sa.Column('outcomes', sa.JSON(), nullable=False),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('reward_breakdown', sa.JSON(), nullable=True),
        sa.Column('observed_at', sa.DateTime(), nullable=False),
        sa.Column('outcome_measured_at', sa.DateTime(), nullable=True),
        sa.Column('source', sa.String(length=100), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_learning_events_tenant_id_tenants')),
        sa.ForeignKeyConstraint(['artist_id'], ['artists.id'], name=op.f('fk_learning_events_artist_id_artists')),
        sa.ForeignKeyConstraint(['release_id'], ['releases.id'], name=op.f('fk_learning_events_release_id_releases')),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_learning_events_campaign_id_campaigns')),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], name=op.f('fk_learning_events_post_id_posts')),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_learning_events_asset_id_assets')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_learning_events')),
    )
    op.create_index(op.f('ix_learning_events_tenant_id'), 'learning_events', ['tenant_id'], unique=False)
    op.create_index('ix_learning_events_tenant_action_observed', 'learning_events', ['tenant_id', 'action_type', 'observed_at'], unique=False)
    op.create_index('ix_learning_events_tenant_artist', 'learning_events', ['tenant_id', 'artist_id'], unique=False)

    # ── post_feature_vectors ─────────────────────────────────────
    op.create_table('post_feature_vectors',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('post_id', sa.Uuid(), nullable=False),
        sa.Column('artist_id', sa.Uuid(), nullable=True),
        sa.Column('campaign_id', sa.Uuid(), nullable=True),
        sa.Column('features', sa.JSON(), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=True),
        sa.Column('content_type', sa.String(length=30), nullable=True),
        sa.Column('post_hour', sa.Integer(), nullable=True),
        sa.Column('post_day_of_week', sa.Integer(), nullable=True),
        sa.Column('caption_length', sa.Integer(), nullable=True),
        sa.Column('hashtag_count', sa.Integer(), nullable=True),
        sa.Column('has_link', sa.Boolean(), nullable=True),
        sa.Column('campaign_phase', sa.String(length=30), nullable=True),
        sa.Column('extracted_at', sa.DateTime(), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_post_feature_vectors_tenant_id_tenants')),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], name=op.f('fk_post_feature_vectors_post_id_posts')),
        sa.ForeignKeyConstraint(['artist_id'], ['artists.id'], name=op.f('fk_post_feature_vectors_artist_id_artists')),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], name=op.f('fk_post_feature_vectors_campaign_id_campaigns')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_post_feature_vectors')),
        sa.UniqueConstraint('post_id', name='uq_post_feature_vectors_post_id'),
    )
    op.create_index(op.f('ix_post_feature_vectors_tenant_id'), 'post_feature_vectors', ['tenant_id'], unique=False)

    # ── post_outcomes ────────────────────────────────────────────
    op.create_table('post_outcomes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('post_id', sa.Uuid(), nullable=False),
        sa.Column('measurement_window', sa.String(length=20), nullable=False),
        sa.Column('impressions', sa.Integer(), nullable=True),
        sa.Column('reach', sa.Integer(), nullable=True),
        sa.Column('engagements', sa.Integer(), nullable=True),
        sa.Column('saves', sa.Integer(), nullable=True),
        sa.Column('shares', sa.Integer(), nullable=True),
        sa.Column('clicks', sa.Integer(), nullable=True),
        sa.Column('comments_count', sa.Integer(), nullable=True),
        sa.Column('followers_gained', sa.Integer(), nullable=True),
        sa.Column('engagement_rate', sa.Float(), nullable=True),
        sa.Column('save_rate', sa.Float(), nullable=True),
        sa.Column('click_through_rate', sa.Float(), nullable=True),
        sa.Column('reward', sa.Float(), nullable=True),
        sa.Column('reward_breakdown', sa.JSON(), nullable=True),
        sa.Column('measured_at', sa.DateTime(), nullable=False),
        sa.Column('source', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_post_outcomes_tenant_id_tenants')),
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], name=op.f('fk_post_outcomes_post_id_posts')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_post_outcomes')),
        sa.UniqueConstraint('post_id', 'measurement_window', name='uq_post_outcomes_post_window'),
    )
    op.create_index(op.f('ix_post_outcomes_tenant_id'), 'post_outcomes', ['tenant_id'], unique=False)
    op.create_index('ix_post_outcomes_tenant_post', 'post_outcomes', ['tenant_id', 'post_id'], unique=False)

    # ── tenant_patterns ──────────────────────────────────────────
    op.create_table('tenant_patterns',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('artist_id', sa.Uuid(), nullable=True),
        sa.Column('pattern_type', sa.String(length=30), nullable=False),
        sa.Column('subject_feature', sa.String(length=100), nullable=True),
        sa.Column('subject_value', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('supporting_observation_count', sa.Integer(), nullable=False),
        sa.Column('observation_window_start', sa.DateTime(), nullable=True),
        sa.Column('observation_window_end', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('superseded_by_id', sa.Uuid(), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_tenant_patterns_tenant_id_tenants')),
        sa.ForeignKeyConstraint(['artist_id'], ['artists.id'], name=op.f('fk_tenant_patterns_artist_id_artists')),
        sa.ForeignKeyConstraint(['superseded_by_id'], ['tenant_patterns.id'], name=op.f('fk_tenant_patterns_superseded_by_id_tenant_patterns')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tenant_patterns')),
    )
    op.create_index(op.f('ix_tenant_patterns_tenant_id'), 'tenant_patterns', ['tenant_id'], unique=False)
    op.create_index('ix_tenant_patterns_tenant_type_feature', 'tenant_patterns', ['tenant_id', 'pattern_type', 'subject_feature'], unique=False)

    # ── global_pattern_stats ─────────────────────────────────────
    op.create_table('global_pattern_stats',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('feature_name', sa.String(length=100), nullable=False),
        sa.Column('feature_value', sa.String(length=255), nullable=False),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('mean_reward', sa.Float(), nullable=False),
        sa.Column('std_dev', sa.Float(), nullable=True),
        sa.Column('confidence_lower', sa.Float(), nullable=True),
        sa.Column('confidence_upper', sa.Float(), nullable=True),
        sa.Column('observation_count', sa.Integer(), nullable=False),
        sa.Column('tenant_count', sa.Integer(), nullable=False),
        sa.Column('k_anonymity_threshold', sa.Integer(), nullable=False),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_global_pattern_stats')),
        sa.UniqueConstraint('feature_name', 'feature_value', 'action_type', name='uq_global_pattern_stats_feature_action'),
    )

    # ── cohort_definitions ───────────────────────────────────────
    op.create_table('cohort_definitions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('criteria', sa.JSON(), nullable=False),
        sa.Column('member_count', sa.Integer(), nullable=False),
        sa.Column('last_computed_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_cohort_definitions_tenant_id_tenants')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cohort_definitions')),
    )
    op.create_index(op.f('ix_cohort_definitions_tenant_id'), 'cohort_definitions', ['tenant_id'], unique=False)

    # ── prompt_versions ──────────────────────────────────────────
    op.create_table('prompt_versions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('prompt_key', sa.String(length=100), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('template', sa.Text(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('model_id', sa.String(length=100), nullable=True),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('author', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt_versions')),
        sa.UniqueConstraint('prompt_key', 'version', name='uq_prompt_versions_key_version'),
    )
    op.create_index(op.f('ix_prompt_versions_prompt_key'), 'prompt_versions', ['prompt_key'], unique=False)

    # ── prompt_assignments ───────────────────────────────────────
    op.create_table('prompt_assignments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('prompt_key', sa.String(length=100), nullable=False),
        sa.Column('prompt_version_id', sa.Uuid(), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=False),
        sa.Column('assigned_by', sa.Uuid(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_prompt_assignments_tenant_id_tenants')),
        sa.ForeignKeyConstraint(['prompt_version_id'], ['prompt_versions.id'], name=op.f('fk_prompt_assignments_prompt_version_id_prompt_versions')),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_prompt_assignments_assigned_by_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_prompt_assignments')),
        sa.UniqueConstraint('tenant_id', 'prompt_key', name='uq_prompt_assignments_tenant_key'),
    )
    op.create_index(op.f('ix_prompt_assignments_tenant_id'), 'prompt_assignments', ['tenant_id'], unique=False)

    # ── evaluation_runs ──────────────────────────────────────────
    op.create_table('evaluation_runs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('evaluation_type', sa.String(length=50), nullable=False),
        sa.Column('scope', sa.String(length=50), nullable=False),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('window_end', sa.DateTime(), nullable=False),
        sa.Column('sample_size', sa.Integer(), nullable=False),
        sa.Column('mae', sa.Float(), nullable=True),
        sa.Column('rank_correlation', sa.Float(), nullable=True),
        sa.Column('top_k_precision', sa.Float(), nullable=True),
        sa.Column('top_k', sa.Integer(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_evaluation_runs_tenant_id_tenants')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_evaluation_runs')),
    )
    op.create_index(op.f('ix_evaluation_runs_tenant_id'), 'evaluation_runs', ['tenant_id'], unique=False)

    # ── evaluation_results ───────────────────────────────────────
    op.create_table('evaluation_results',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('evaluation_run_id', sa.Uuid(), nullable=False),
        sa.Column('action_id', sa.Uuid(), nullable=True),
        sa.Column('action_type', sa.String(length=50), nullable=False),
        sa.Column('predicted_score', sa.Float(), nullable=False),
        sa.Column('predicted_rank', sa.Integer(), nullable=True),
        sa.Column('actual_reward', sa.Float(), nullable=True),
        sa.Column('actual_rank', sa.Integer(), nullable=True),
        sa.Column('absolute_error', sa.Float(), nullable=True),
        sa.Column('explanation', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['evaluation_run_id'], ['evaluation_runs.id'], name=op.f('fk_evaluation_results_evaluation_run_id_evaluation_runs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_evaluation_results')),
    )
    op.create_index('ix_evaluation_results_run', 'evaluation_results', ['evaluation_run_id'], unique=False)

    # ── learning_audit_log ───────────────────────────────────────
    op.create_table('learning_audit_log',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.Uuid(), nullable=True),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_learning_audit_log_tenant_id_tenants')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_learning_audit_log_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_learning_audit_log')),
    )
    op.create_index(op.f('ix_learning_audit_log_tenant_id'), 'learning_audit_log', ['tenant_id'], unique=False)
    op.create_index('ix_learning_audit_log_tenant_action', 'learning_audit_log', ['tenant_id', 'action'], unique=False)
    op.create_index('ix_learning_audit_log_tenant_entity', 'learning_audit_log', ['tenant_id', 'entity_type', 'entity_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_learning_audit_log_tenant_entity', table_name='learning_audit_log')
    op.drop_index('ix_learning_audit_log_tenant_action', table_name='learning_audit_log')
    op.drop_index(op.f('ix_learning_audit_log_tenant_id'), table_name='learning_audit_log')
    op.drop_table('learning_audit_log')

    op.drop_index('ix_evaluation_results_run', table_name='evaluation_results')
    op.drop_table('evaluation_results')

    op.drop_index(op.f('ix_evaluation_runs_tenant_id'), table_name='evaluation_runs')
    op.drop_table('evaluation_runs')

    op.drop_index(op.f('ix_prompt_assignments_tenant_id'), table_name='prompt_assignments')
    op.drop_table('prompt_assignments')

    op.drop_index(op.f('ix_prompt_versions_prompt_key'), table_name='prompt_versions')
    op.drop_table('prompt_versions')

    op.drop_index(op.f('ix_cohort_definitions_tenant_id'), table_name='cohort_definitions')
    op.drop_table('cohort_definitions')

    op.drop_table('global_pattern_stats')

    op.drop_index('ix_tenant_patterns_tenant_type_feature', table_name='tenant_patterns')
    op.drop_index(op.f('ix_tenant_patterns_tenant_id'), table_name='tenant_patterns')
    op.drop_table('tenant_patterns')

    op.drop_index('ix_post_outcomes_tenant_post', table_name='post_outcomes')
    op.drop_index(op.f('ix_post_outcomes_tenant_id'), table_name='post_outcomes')
    op.drop_table('post_outcomes')

    op.drop_index(op.f('ix_post_feature_vectors_tenant_id'), table_name='post_feature_vectors')
    op.drop_table('post_feature_vectors')

    op.drop_index('ix_learning_events_tenant_artist', table_name='learning_events')
    op.drop_index('ix_learning_events_tenant_action_observed', table_name='learning_events')
    op.drop_index(op.f('ix_learning_events_tenant_id'), table_name='learning_events')
    op.drop_table('learning_events')
