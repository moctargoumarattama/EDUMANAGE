"""add_inscription_id_to_paiement_and_frais_annuels_to_inscription

Revision ID: 15ae5fdad90d
Revises: 3864936b0572
Create Date: 2026-09-12 19:16:55.568659

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '15ae5fdad90d'
down_revision = '3864936b0572'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Table paiement : ajout colonne inscription_id avec FK vers inscriptions(id) et index
    with op.batch_alter_table('paiement', schema=None) as batch_op:
        batch_op.add_column(sa.Column('inscription_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_paiement_inscription_id',
            'inscriptions',
            ['inscription_id'],
            ['id'],
            ondelete='RESTRICT'
        )
        batch_op.create_index('ix_paiement_inscription_id', ['inscription_id'], unique=False)

    # 2. Table inscriptions : ajout colonne frais_annuels
    with op.batch_alter_table('inscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('frais_annuels', sa.Float(), nullable=True))


def downgrade():
    # 1. Table inscriptions : suppression colonne frais_annuels
    with op.batch_alter_table('inscriptions', schema=None) as batch_op:
        batch_op.drop_column('frais_annuels')

    # 2. Table paiement : suppression index, FK et colonne inscription_id
    with op.batch_alter_table('paiement', schema=None) as batch_op:
        batch_op.drop_index('ix_paiement_inscription_id')
        batch_op.drop_constraint('fk_paiement_inscription_id', type_='foreignkey')
        batch_op.drop_column('inscription_id')
