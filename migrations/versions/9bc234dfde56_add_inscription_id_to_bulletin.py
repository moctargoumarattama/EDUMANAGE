"""add_inscription_id_to_bulletin

Revision ID: 9bc234dfde56
Revises: 8ab121cfcb45
Create Date: 2026-09-12 20:10:00.000000

Phase 3D : Annualisation du module Bulletins via Bulletin.inscription_id.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9bc234dfde56'
down_revision = '8ab121cfcb45'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Nettoyage des éventuels anciens bulletins de dev orphelins sans inscription
    conn.execute(sa.text("DELETE FROM bulletin WHERE eleve_id NOT IN (SELECT id FROM eleve)"))

    # 2. Modification de la table bulletin par recopie SQLite (batch_alter_table)
    with op.batch_alter_table('bulletin', schema=None) as batch_op:
        batch_op.add_column(sa.Column('inscription_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('classe_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('annee_scolaire_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('periode', sa.String(length=50), nullable=True, server_default='Trimestre 1'))
        batch_op.add_column(sa.Column('moyenne_generale', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('rang', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('rang_total', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('appreciation_generale', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('statut', sa.String(length=20), server_default='valide', nullable=False))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))

        # Permettre aux colonnes legacy d'être nullables
        batch_op.alter_column('matiere', existing_type=sa.String(length=100), nullable=True)
        batch_op.alter_column('note', existing_type=sa.Float(), nullable=True)
        batch_op.alter_column('annee', existing_type=sa.String(length=20), nullable=True)

        # Clés étrangères
        batch_op.create_foreign_key(
            'fk_bulletin_inscription_id',
            'inscriptions',
            ['inscription_id'],
            ['id'],
            ondelete='RESTRICT'
        )
        batch_op.create_foreign_key(
            'fk_bulletin_classe_id',
            'classe',
            ['classe_id'],
            ['id'],
            ondelete='RESTRICT'
        )
        batch_op.create_foreign_key(
            'fk_bulletin_annee_scolaire_id',
            'annee_scolaire',
            ['annee_scolaire_id'],
            ['id'],
            ondelete='RESTRICT'
        )

        # Index
        batch_op.create_index('ix_bulletin_inscription_id', ['inscription_id'], unique=False)
        batch_op.create_index('ix_bulletin_inscription_periode', ['inscription_id', 'periode'], unique=False)
        batch_op.create_index('ix_bulletin_ecole_annee', ['ecole_id', 'annee_scolaire_id'], unique=False)


def downgrade():
    with op.batch_alter_table('bulletin', schema=None) as batch_op:
        batch_op.drop_index('ix_bulletin_ecole_annee')
        batch_op.drop_index('ix_bulletin_inscription_periode')
        batch_op.drop_index('ix_bulletin_inscription_id')
        batch_op.drop_constraint('fk_bulletin_annee_scolaire_id', type_='foreignkey')
        batch_op.drop_constraint('fk_bulletin_classe_id', type_='foreignkey')
        batch_op.drop_constraint('fk_bulletin_inscription_id', type_='foreignkey')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('statut')
        batch_op.drop_column('appreciation_generale')
        batch_op.drop_column('rang_total')
        batch_op.drop_column('rang')
        batch_op.drop_column('moyenne_generale')
        batch_op.drop_column('periode')
        batch_op.drop_column('annee_scolaire_id')
        batch_op.drop_column('classe_id')
        batch_op.drop_column('inscription_id')

