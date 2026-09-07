"""add motif_blocage to ecole

Revision ID: a8c7e2b1d5f3
Revises: f1b8a9c3d2e4
Create Date: 2026-09-07 22:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8c7e2b1d5f3'
down_revision = 'f1b8a9c3d2e4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.add_column(sa.Column('motif_blocage', sa.String(length=300), nullable=True))


def downgrade():
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.drop_column('motif_blocage')

