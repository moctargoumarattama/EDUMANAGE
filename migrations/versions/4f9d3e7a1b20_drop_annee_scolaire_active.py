"""Drop redundant AnneeScolaire.active column.

Revision ID: 4f9d3e7a1b20
Revises: 31bcd72e800f
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = "4f9d3e7a1b20"
down_revision = "31bcd72e800f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("annee_scolaire", schema=None) as batch_op:
        batch_op.drop_column("active")


def downgrade():
    with op.batch_alter_table("annee_scolaire", schema=None) as batch_op:
        batch_op.add_column(sa.Column("active", sa.Boolean(), nullable=True))