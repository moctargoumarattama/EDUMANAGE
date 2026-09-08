"""add_sync_operation_log

Revision ID: 5f4e3d2c1b0a
Revises: 4e3f1048a0e2
Create Date: 2026-09-08 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f4e3d2c1b0a'
down_revision = '4e3f1048a0e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sync_operation_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_op_id', sa.String(length=64), nullable=False),
        sa.Column('ecole_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='synced'),
        sa.Column('payload_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['ecole_id'], ['ecole.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['utilisateur.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sync_operation_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sync_operation_log_client_op_id'), ['client_op_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_sync_operation_log_ecole_id'), ['ecole_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sync_operation_log_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('sync_operation_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sync_operation_log_user_id'))
        batch_op.drop_index(batch_op.f('ix_sync_operation_log_ecole_id'))
        batch_op.drop_index(batch_op.f('ix_sync_operation_log_client_op_id'))

    op.drop_table('sync_operation_log')
