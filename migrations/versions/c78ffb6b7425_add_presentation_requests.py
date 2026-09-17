"""add presentation requests

Revision ID: c78ffb6b7425
Revises: d8e9f0a1b2c3
Create Date: 2026-09-17 04:14:10.603650
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c78ffb6b7425"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "demande_presentation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nom_ecole", sa.String(length=150), nullable=False),
        sa.Column("telephone", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("ville", sa.String(length=100), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("statut", sa.String(length=30), nullable=False),
        sa.Column("notes_admin", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_demande_presentation_statut",
        "demande_presentation",
        ["statut"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_demande_presentation_statut",
        table_name="demande_presentation",
    )
    op.drop_table("demande_presentation")
