"""add_inscription_id_to_absence

Revision ID: 3864936b0572
Revises: 28cd4856f1c5
Create Date: 2026-09-12 18:07:55.397693

Phase 3A-Bis : Ancrage annuel définitif des absences via Absence.inscription_id.

Règles de la migration :
  - Ajouter inscription_id INTEGER NULLABLE avec FK vers inscription.id (ON DELETE RESTRICT).
  - Créer un index ix_absence_inscription_id sur la nouvelle colonne.
  - Ne PAS supprimer eleve_id, cours_id ni ecole_id existants.
  - Utiliser batch_alter_table pour compatibilité SQLite.
  - Backfill NON inclus ici : la colonne est nullable, les données existantes
    gardent inscription_id = NULL (ambiguïté historique acceptée).

Downgrade : suppression colonne + index via batch_alter_table.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3864936b0572'
down_revision = '28cd4856f1c5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('absence', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'inscription_id',
                sa.Integer(),
                nullable=True
            )
        )
        batch_op.create_index('ix_absence_inscription_id', ['inscription_id'])
        batch_op.create_foreign_key(
            'fk_absence_inscription_id',
            'inscription',
            ['inscription_id'],
            ['id'],
        )


def downgrade():
    with op.batch_alter_table('absence', schema=None) as batch_op:
        batch_op.drop_constraint('fk_absence_inscription_id', type_='foreignkey')
        batch_op.drop_index('ix_absence_inscription_id')
        batch_op.drop_column('inscription_id')
