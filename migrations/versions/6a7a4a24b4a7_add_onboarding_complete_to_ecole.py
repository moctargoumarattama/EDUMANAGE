"""Add onboarding_complete to Ecole

Revision ID: 6a7a4a24b4a7
Revises: 333fb6b8679b
Create Date: 2026-09-19 13:59:30.438166

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a7a4a24b4a7'
down_revision = '333fb6b8679b'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Ajouter la colonne en nullable=True (PostgreSQL/SQLite safe)
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.add_column(sa.Column('onboarding_complete', sa.Boolean(), nullable=True))

    # 2. Backfill des données
    ecole = sa.table('ecole', sa.column('id'), sa.column('onboarding_complete', sa.Boolean()))
    annee = sa.table('annee_scolaire', sa.column('id'), sa.column('ecole_id'), sa.column('statut'))
    config = sa.table('annee_niveau_config', sa.column('ecole_id'), sa.column('annee_scolaire_id'), sa.column('actif', sa.Boolean()))

    # Tout à False par défaut
    op.execute(ecole.update().values(onboarding_complete=False))

    # Identifier les écoles avec année active et config niveau active
    completed_subq = sa.select(annee.c.ecole_id).where(
        annee.c.statut == 'active',
        annee.c.ecole_id == config.c.ecole_id,
        annee.c.id == config.c.annee_scolaire_id,
        config.c.actif == True
    ).distinct()

    op.execute(ecole.update().where(ecole.c.id.in_(completed_subq)).values(onboarding_complete=True))

    # 3. Rendre la colonne nullable=False avec un server_default propre
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.alter_column('onboarding_complete', nullable=False, server_default=sa.sql.expression.false())


def downgrade():
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.drop_column('onboarding_complete')
