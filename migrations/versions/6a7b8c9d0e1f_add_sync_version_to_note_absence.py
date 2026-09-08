"""add_sync_version_to_note_absence

Revision ID: 6a7b8c9d0e1f
Revises: 5f4e3d2c1b0a
Create Date: 2026-09-08 14:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a7b8c9d0e1f'
down_revision = '5f4e3d2c1b0a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('note', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sync_version', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('last_by_admin', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))

    with op.batch_alter_table('absence', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sync_version', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('last_by_admin', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))


def downgrade():
    with op.batch_alter_table('absence', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('last_by_admin')
        batch_op.drop_column('sync_version')

    with op.batch_alter_table('note', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('last_by_admin')
        batch_op.drop_column('sync_version')

