"""Enforce one active academic year per school.

Revision ID: 6a2c8f1d9e34
Revises: 4f9d3e7a1b20
Create Date: 2026-09-01
"""
from alembic import op


revision = "6a2c8f1d9e34"
down_revision = "4f9d3e7a1b20"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE UNIQUE INDEX uq_annee_scolaire_active_per_school "
            "ON annee_scolaire (ecole_id) WHERE statut = 'active'"
        )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.drop_index("uq_annee_scolaire_active_per_school", table_name="annee_scolaire")
