"""allow parent accounts without email

Revision ID: d3e5f7a9b1c2
Revises: d2e4f6a8b0c1
Create Date: 2026-09-26 22:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd3e5f7a9b1c2'
down_revision = 'd2e4f6a8b0c1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.alter_column(
            'email',
            existing_type=sa.String(length=120),
            nullable=True,
        )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE utilisateur "
            "SET email = 'parent_' || id || '@klasora.local' "
            "WHERE email IS NULL"
        )
    )
    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.alter_column(
            'email',
            existing_type=sa.String(length=120),
            nullable=False,
        )
