"""phase 3.6 channel analyses

Revision ID: 19ef5d0fb95e
Revises: 7e3f5a9c1b24
Create Date: 2026-10-14 01:28:56.224608+00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '19ef5d0fb95e'
down_revision: str | None = '7e3f5a9c1b24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ai_level = postgresql.ENUM('low', 'medium', 'high', name='ai_level', create_type=False)  # since 3.5
    op.create_table('channel_analyses',
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('prompt_template_id', sa.BigInteger(), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('model_key', sa.String(length=60), nullable=False),
    sa.Column('ai_call_id', sa.BigInteger(), nullable=True),
    sa.Column('detail', sa.String(length=10), nullable=False),
    sa.Column('inputs_digest', sa.String(length=64), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('video_ids', postgresql.ARRAY(sa.BigInteger()), nullable=False),
    sa.Column('thumbnail_analysis_ids', postgresql.ARRAY(sa.BigInteger()), nullable=False),
    sa.Column('visual_consistency', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('repeated_template', sa.Boolean(), nullable=False),
    sa.Column('outdated_style_score', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('has_design_system', sa.Boolean(), nullable=False),
    sa.Column('visual_opportunity', ai_level, nullable=False),
    sa.Column('patterns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recurring_problems', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recurring_problem_codes', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('weakest_video_ids', postgresql.ARRAY(sa.BigInteger()), nullable=False),
    sa.Column('redesign_candidates', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('cardinality(video_ids) >= 1', name=op.f('ck_channel_analyses_video_ids_not_empty')),
    sa.CheckConstraint('outdated_style_score BETWEEN 1 AND 10', name=op.f('ck_channel_analyses_outdated_style_score_range')),
    sa.CheckConstraint('visual_consistency BETWEEN 1 AND 10', name=op.f('ck_channel_analyses_visual_consistency_range')),
    sa.ForeignKeyConstraint(['ai_call_id'], ['ai_calls.id'], name=op.f('fk_channel_analyses_ai_call_id_ai_calls'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_channel_analyses_channel_id_channels'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['prompt_template_id'], ['prompt_templates.id'], name=op.f('fk_channel_analyses_prompt_template_id_prompt_templates')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_channel_analyses')),
    sa.UniqueConstraint('channel_id', 'inputs_digest', 'prompt_template_id', 'model_key', name='uq_channel_analyses_cache_key')
    )
    op.create_index('ix_channel_analyses_problem_codes', 'channel_analyses', ['recurring_problem_codes'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_channel_analyses_visual_opportunity'), 'channel_analyses', ['visual_opportunity'], unique=False)
    op.create_index('uq_channel_analyses_current', 'channel_analyses', ['channel_id'], unique=True, postgresql_where=sa.text('is_current'))


def downgrade() -> None:
    op.drop_index('uq_channel_analyses_current', table_name='channel_analyses', postgresql_where=sa.text('is_current'))
    op.drop_index(op.f('ix_channel_analyses_visual_opportunity'), table_name='channel_analyses')
    op.drop_index('ix_channel_analyses_problem_codes', table_name='channel_analyses', postgresql_using='gin')
    op.drop_table('channel_analyses')
