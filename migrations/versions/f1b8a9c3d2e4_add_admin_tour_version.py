"""add admin_tour_version to utilisateur

Revision ID: f1b8a9c3d2e4
Revises: e5a8b92c4f10
Create Date: 2026-09-07 21:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1b8a9c3d2e4'
down_revision = 'e5a8b92c4f10'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.add_column(sa.Column('admin_tour_version', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('utilisateur', schema=None) as batch_op:
        batch_op.drop_column('admin_tour_version')
