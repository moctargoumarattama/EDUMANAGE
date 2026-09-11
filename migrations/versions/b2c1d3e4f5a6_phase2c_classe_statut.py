"""phase2c classe statut ouverte fermee

Revision ID: b2c1d3e4f5a6
Revises: a7e4c2d9f1b0
Create Date: 2026-09-11 19:02:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b2c1d3e4f5a6"
down_revision = "a7e4c2d9f1b0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("classe") as batch_op:
        batch_op.add_column(
            sa.Column("statut", sa.String(length=20), nullable=False, server_default="ouverte")
        )
        batch_op.create_index("ix_classe_ecole_annee_statut", ["ecole_id", "annee_scolaire_id", "statut"])


def downgrade():
    with op.batch_alter_table("classe") as batch_op:
        batch_op.drop_index("ix_classe_ecole_annee_statut")
        batch_op.drop_column("statut")
