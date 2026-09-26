"""phone_first_auth

Revision ID: e6f7a8b9c0d1
Revises: d3e5f7a9b1c2
Create Date: 2026-09-26 23:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e6f7a8b9c0d1'
down_revision = 'd3e5f7a9b1c2'
branch_labels = None
depends_on = None


def _index_names(table_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {idx['name'] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()

    bind.execute(sa.text("""
        UPDATE utilisateur
        SET telephone = (
            SELECT professeur.telephone
            FROM professeur
            WHERE professeur.utilisateur_id = utilisateur.id
              AND professeur.telephone IS NOT NULL
              AND professeur.telephone != ''
            LIMIT 1
        )
        WHERE (telephone IS NULL OR telephone = '')
          AND role = 'professeur'
          AND EXISTS (
              SELECT 1 FROM professeur
              WHERE professeur.utilisateur_id = utilisateur.id
                AND professeur.telephone IS NOT NULL
                AND professeur.telephone != ''
          )
    """))

    bind.execute(sa.text("""
        UPDATE utilisateur
        SET telephone = (
            SELECT eleve.contact_parent
            FROM eleve
            WHERE eleve.parent_id = utilisateur.id
              AND eleve.contact_parent IS NOT NULL
              AND eleve.contact_parent != ''
            LIMIT 1
        )
        WHERE (telephone IS NULL OR telephone = '')
          AND role = 'parent'
          AND EXISTS (
              SELECT 1 FROM eleve
              WHERE eleve.parent_id = utilisateur.id
                AND eleve.contact_parent IS NOT NULL
                AND eleve.contact_parent != ''
          )
    """))

    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.alter_column(
            'telephone',
            existing_type=sa.String(length=20),
            type_=sa.String(length=30),
            existing_nullable=True,
        )

    if 'ix_utilisateur_telephone' not in _index_names('utilisateur'):
        with op.batch_alter_table('utilisateur', schema=None) as batch_op:
            batch_op.create_index('ix_utilisateur_telephone', ['telephone'], unique=True)


def downgrade():
    if 'ix_utilisateur_telephone' in _index_names('utilisateur'):
        with op.batch_alter_table('utilisateur', schema=None) as batch_op:
            batch_op.drop_index('ix_utilisateur_telephone')

    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.alter_column(
            'telephone',
            existing_type=sa.String(length=30),
            type_=sa.String(length=20),
            existing_nullable=True,
        )
