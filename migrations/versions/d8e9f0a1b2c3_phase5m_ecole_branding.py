"""phase5m_ecole_branding

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-13 20:44:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd8e9f0a1b2c3'
down_revision = 'c7d8e9f0a1b2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.add_column(sa.Column('signature_path', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('cachet_path', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('ville', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        batch_op.drop_column('ville')
        batch_op.drop_column('cachet_path')
        batch_op.drop_column('signature_path')

