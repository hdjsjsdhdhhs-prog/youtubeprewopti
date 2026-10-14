"""phase 3.7 leads, lead scores, status history, ideal lead profiles

Revision ID: 5c2d8e4f7a13
Revises: 19ef5d0fb95e
Create Date: 2026-10-14 03:00:00.000000+00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '5c2d8e4f7a13'
down_revision: str | None = '19ef5d0fb95e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEAD_STATUSES = (
    'discovered', 'analyzed', 'shortlisted', 'selected', 'generation_pending', 'generated', 'offer_draft',
    'pending_approval', 'approved', 'scheduled', 'sent', 'replied', 'declined', 'opted_out', 'failed',
    'rejected',
)
COMPONENTS = (
    'thumbnail_opportunity', 'channel_fit', 'audience', 'views', 'activity', 'growth', 'contactability',
    'niche_value',
)


def upgrade() -> None:
    postgresql.ENUM(*LEAD_STATUSES, name='lead_status').create(op.get_bind(), checkfirst=True)
    lead_status = postgresql.ENUM(*LEAD_STATUSES, name='lead_status', create_type=False)

    op.create_table('ideal_lead_profiles',
    sa.Column('workspace_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('criteria', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_ideal_lead_profiles_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_ideal_lead_profiles_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ideal_lead_profiles')),
    sa.UniqueConstraint('workspace_id', 'name', name=op.f('uq_ideal_lead_profiles_workspace_id_name'))
    )
    op.create_index('uq_ideal_lead_profiles_default', 'ideal_lead_profiles', ['workspace_id'], unique=True, postgresql_where=sa.text('is_default'))

    op.create_table('leads',
    sa.Column('workspace_id', sa.BigInteger(), nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('status', lead_status, server_default='discovered', nullable=False),
    sa.Column('status_changed_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_from_project_id', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_leads_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_from_project_id'], ['search_projects.id'], name=op.f('fk_leads_created_from_project_id_search_projects'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_leads_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads')),
    sa.UniqueConstraint('workspace_id', 'channel_id', name=op.f('uq_leads_workspace_id_channel_id'))
    )
    op.create_index(op.f('ix_leads_channel_id'), 'leads', ['channel_id'], unique=False)
    op.create_index('ix_leads_workspace_status', 'leads', ['workspace_id', 'status'], unique=False)

    op.create_table('lead_scores',
    sa.Column('lead_id', sa.BigInteger(), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('lead_profile_version', sa.Integer(), nullable=False),
    sa.Column('priority_profile_version', sa.Integer(), nullable=False),
    sa.Column('thumbnail_profile_version', sa.Integer(), nullable=False),
    sa.Column('ideal_lead_profile_id', sa.BigInteger(), nullable=True),
    sa.Column('total', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('priority_score', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('data_coverage', sa.Numeric(precision=5, scale=2), nullable=False),
    *(sa.Column(c, sa.Numeric(precision=5, scale=2), nullable=False) for c in COMPONENTS),
    sa.Column('avg_thumbnail_score', sa.Numeric(precision=4, scale=2), nullable=True),
    sa.Column('components', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('total BETWEEN 0 AND 100', name=op.f('ck_lead_scores_total_range')),
    sa.CheckConstraint('priority_score BETWEEN 0 AND 100', name=op.f('ck_lead_scores_priority_score_range')),
    sa.CheckConstraint('data_coverage BETWEEN 0 AND 100', name=op.f('ck_lead_scores_data_coverage_range')),
    sa.CheckConstraint('avg_thumbnail_score BETWEEN 1 AND 10', name=op.f('ck_lead_scores_avg_thumbnail_score_range')),
    *(sa.CheckConstraint(f'{c} BETWEEN 0 AND 100', name=op.f(f'ck_lead_scores_{c}_range')) for c in COMPONENTS),
    sa.ForeignKeyConstraint(['ideal_lead_profile_id'], ['ideal_lead_profiles.id'], name=op.f('fk_lead_scores_ideal_lead_profile_id_ideal_lead_profiles'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_lead_scores_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lead_scores'))
    )
    op.create_index('uq_lead_scores_current', 'lead_scores', ['lead_id'], unique=True, postgresql_where=sa.text('is_current'))
    op.create_index('ix_lead_scores_total', 'lead_scores', ['total'], unique=False)
    op.create_index('ix_lead_scores_priority_score', 'lead_scores', ['priority_score'], unique=False)
    op.create_index(op.f('ix_lead_scores_avg_thumbnail_score'), 'lead_scores', ['avg_thumbnail_score'], unique=False)

    op.create_table('lead_status_history',
    sa.Column('lead_id', sa.BigInteger(), nullable=False),
    sa.Column('from_status', lead_status, nullable=True),
    sa.Column('to_status', lead_status, nullable=False),
    sa.Column('actor_user_id', sa.BigInteger(), nullable=True),
    sa.Column('reason', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_lead_status_history_actor_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_lead_status_history_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lead_status_history'))
    )
    op.create_index(op.f('ix_lead_status_history_lead_id'), 'lead_status_history', ['lead_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_lead_status_history_lead_id'), table_name='lead_status_history')
    op.drop_table('lead_status_history')
    op.drop_index(op.f('ix_lead_scores_avg_thumbnail_score'), table_name='lead_scores')
    op.drop_index('ix_lead_scores_priority_score', table_name='lead_scores')
    op.drop_index('ix_lead_scores_total', table_name='lead_scores')
    op.drop_index('uq_lead_scores_current', table_name='lead_scores', postgresql_where=sa.text('is_current'))
    op.drop_table('lead_scores')
    op.drop_index('ix_leads_workspace_status', table_name='leads')
    op.drop_index(op.f('ix_leads_channel_id'), table_name='leads')
    op.drop_table('leads')
    op.drop_index('uq_ideal_lead_profiles_default', table_name='ideal_lead_profiles', postgresql_where=sa.text('is_default'))
    op.drop_table('ideal_lead_profiles')
    postgresql.ENUM(name='lead_status').drop(op.get_bind(), checkfirst=True)
