"""phase2c5c annee niveau config

Revision ID: c3d4e5f6a7b8
Revises: b2c1d3e4f5a6
Create Date: 2026-09-12 03:25:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c1d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'annee_niveau_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ecole_id', sa.Integer(), nullable=False),
        sa.Column('annee_scolaire_id', sa.Integer(), nullable=False),
        sa.Column('niveau_id', sa.Integer(), nullable=False),
        sa.Column('actif', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['annee_scolaire_id'], ['annee_scolaire.id']),
        sa.ForeignKeyConstraint(['ecole_id'], ['ecole.id']),
        sa.ForeignKeyConstraint(['niveau_id'], ['niveau_scolaire.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ecole_id', 'annee_scolaire_id', 'niveau_id', name='uq_annee_niveau_config'),
    )
    op.create_index('ix_annee_niveau_config_annee_actif', 'annee_niveau_config', ['annee_scolaire_id', 'actif'])
    op.create_index('ix_annee_niveau_config_ecole_annee', 'annee_niveau_config', ['ecole_id', 'annee_scolaire_id'])


def downgrade():
    op.drop_index('ix_annee_niveau_config_ecole_annee', table_name='annee_niveau_config')
    op.drop_index('ix_annee_niveau_config_annee_actif', table_name='annee_niveau_config')
    op.drop_table('annee_niveau_config')
