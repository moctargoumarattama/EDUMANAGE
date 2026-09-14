"""phase5h_periode_bulletin_dates

Revision ID: c7d8e9f0a1b2
Revises: 9bc234dfde56
Create Date: 2026-09-13 16:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d8e9f0a1b2'
down_revision = '9bc234dfde56'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('periode_bulletin', schema=None) as batch_op:
        batch_op.add_column(sa.Column('date_debut', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('date_fin', sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table('periode_bulletin', schema=None) as batch_op:
        batch_op.drop_column('date_fin')
        batch_op.drop_column('date_debut')
