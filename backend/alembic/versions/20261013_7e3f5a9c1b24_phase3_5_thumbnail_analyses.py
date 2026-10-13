"""phase 3.5 thumbnail analyses and scoring profiles

Revision ID: 7e3f5a9c1b24
Revises: 0d773a65d5b6
Create Date: 2026-10-13 22:00:00.000000+00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '7e3f5a9c1b24'
down_revision: str | None = '0d773a65d5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBSCORES = (
    'composition_score', 'contrast_score', 'text_score', 'hierarchy_score', 'subject_score', 'color_score',
    'mobile_readability', 'topic_relevance', 'visual_impact', 'professionalism', 'redesign_potential',
)


def upgrade() -> None:
    ai_level = postgresql.ENUM('low', 'medium', 'high', name='ai_level', create_type=False)
    ai_level.create(op.get_bind(), checkfirst=True)

    op.create_table('scoring_profiles',
    sa.Column('workspace_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('thumbnail', 'lead', 'priority', 'contactability', name='scoring_kind'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('weights', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('version >= 1', name=op.f('ck_scoring_profiles_version_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_scoring_profiles_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_scoring_profiles_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scoring_profiles')),
    sa.UniqueConstraint('workspace_id', 'kind', 'version', name=op.f('uq_scoring_profiles_workspace_id_kind_version'))
    )
    op.create_index('uq_scoring_profiles_active', 'scoring_profiles', ['workspace_id', 'kind'], unique=True, postgresql_where=sa.text('is_active'))

    op.create_table('thumbnail_analyses',
    sa.Column('image_asset_id', sa.BigInteger(), nullable=False),
    sa.Column('prompt_template_id', sa.BigInteger(), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('model_key', sa.String(length=60), nullable=False),
    sa.Column('ai_call_id', sa.BigInteger(), nullable=True),
    sa.Column('reused_from_id', sa.BigInteger(), nullable=True),
    sa.Column('phash_distance', sa.Integer(), nullable=True),
    sa.Column('detail', sa.String(length=10), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default='false', nullable=False),
    *(sa.Column(name, sa.Numeric(precision=4, scale=2), nullable=False) for name in SUBSCORES),
    sa.Column('has_face', sa.Boolean(), nullable=False),
    sa.Column('face_emotion', sa.String(length=60), nullable=True),
    sa.Column('text_present', sa.Boolean(), nullable=False),
    sa.Column('text_word_count_est', sa.Integer(), nullable=False),
    sa.Column('focal_point', sa.Text(), nullable=False),
    sa.Column('background_separation', ai_level, nullable=False),
    sa.Column('clutter_level', ai_level, nullable=False),
    sa.Column('improvement_opportunity', ai_level, nullable=False),
    sa.Column('problems', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recommendations', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('problem_codes', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('visual_summary', sa.Text(), nullable=False),
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    *(sa.CheckConstraint(f'{name} BETWEEN 1 AND 10', name=op.f(f'ck_thumbnail_analyses_{name}_range')) for name in SUBSCORES),
    sa.CheckConstraint('text_word_count_est >= 0', name=op.f('ck_thumbnail_analyses_word_count_nonneg')),
    sa.ForeignKeyConstraint(['ai_call_id'], ['ai_calls.id'], name=op.f('fk_thumbnail_analyses_ai_call_id_ai_calls'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['image_asset_id'], ['image_assets.id'], name=op.f('fk_thumbnail_analyses_image_asset_id_image_assets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['prompt_template_id'], ['prompt_templates.id'], name=op.f('fk_thumbnail_analyses_prompt_template_id_prompt_templates')),
    sa.ForeignKeyConstraint(['reused_from_id'], ['thumbnail_analyses.id'], name=op.f('fk_thumbnail_analyses_reused_from_id_thumbnail_analyses'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thumbnail_analyses')),
    sa.UniqueConstraint('image_asset_id', 'prompt_template_id', 'model_key', name='uq_thumbnail_analyses_cache_key')
    )
    op.create_index(op.f('ix_thumbnail_analyses_improvement_opportunity'), 'thumbnail_analyses', ['improvement_opportunity'], unique=False)
    op.create_index('ix_thumbnail_analyses_problem_codes', 'thumbnail_analyses', ['problem_codes'], unique=False, postgresql_using='gin')
    op.create_index('uq_thumbnail_analyses_current', 'thumbnail_analyses', ['image_asset_id'], unique=True, postgresql_where=sa.text('is_current'))


def downgrade() -> None:
    op.drop_index('uq_thumbnail_analyses_current', table_name='thumbnail_analyses', postgresql_where=sa.text('is_current'))
    op.drop_index('ix_thumbnail_analyses_problem_codes', table_name='thumbnail_analyses', postgresql_using='gin')
    op.drop_index(op.f('ix_thumbnail_analyses_improvement_opportunity'), table_name='thumbnail_analyses')
    op.drop_table('thumbnail_analyses')
    op.drop_index('uq_scoring_profiles_active', table_name='scoring_profiles', postgresql_where=sa.text('is_active'))
    op.drop_table('scoring_profiles')
    for name in ('ai_level', 'scoring_kind'):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
