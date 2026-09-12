"""allow nullable eleve classe cache

Revision ID: 28cd4856f1c5
Revises: c3d4e5f6a7b8
Create Date: 2026-09-12 16:22:57.831225

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '28cd4856f1c5'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('eleve', schema=None) as batch_op:
        batch_op.alter_column(
            'classe_id',
            existing_type=sa.Integer(),
            nullable=True
        )


def downgrade():
    bind = op.get_bind()
    null_count = bind.execute(sa.text("SELECT COUNT(*) FROM eleve WHERE classe_id IS NULL")).scalar()
    if null_count and null_count > 0:
        raise RuntimeError(
            f"Impossible de rétrograder la migration: {null_count} élève(s) ont classe_id NULL. "
            "La contrainte NOT NULL ne peut pas être rétablie sans assignation préalable d'une classe."
        )

    with op.batch_alter_table('eleve', schema=None) as batch_op:
        batch_op.alter_column(
            'classe_id',
            existing_type=sa.Integer(),
            nullable=False
        )
