"""add disabled_at to ecole

Revision ID: c1d2e3f4a6b7
Revises: b4c5d6e7f8a9
Create Date: 2026-09-20 13:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c1d2e3f4a6b7'
down_revision = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.add_column(sa.Column('disabled_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.drop_column('disabled_at')
