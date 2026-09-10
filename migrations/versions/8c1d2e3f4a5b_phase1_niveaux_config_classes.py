"""phase1 niveaux config classes

Revision ID: 8c1d2e3f4a5b
Revises: 344198f5585e
Create Date: 2026-09-10 16:23:30.000000
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime


revision = "8c1d2e3f4a5b"
down_revision = "344198f5585e"
branch_labels = None
depends_on = None


STANDARD_NIVEAUX = [
    ("CI", "CI", "primaire", 1),
    ("CP", "CP", "primaire", 2),
    ("CE1", "CE1", "primaire", 3),
    ("CE2", "CE2", "primaire", 4),
    ("CM1", "CM1", "primaire", 5),
    ("CM2", "CM2", "primaire", 6),
    ("6E", "6e", "college", 7),
    ("5E", "5e", "college", 8),
    ("4E", "4e", "college", 9),
    ("3E", "3e", "college", 10),
    ("2NDE", "2nde", "lycee", 11),
    ("1ERE", "1ere", "lycee", 12),
    ("TERMINALE", "Terminale", "lycee", 13),
]


def upgrade():
    op.create_table(
        "niveau_scolaire",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("nom", sa.String(length=50), nullable=False),
        sa.Column("cycle", sa.String(length=20), nullable=False),
        sa.Column("ordre", sa.Integer(), nullable=False),
        sa.Column("niveau_suivant_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["niveau_suivant_id"], ["niveau_scolaire.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_niveau_scolaire_ordre", "niveau_scolaire", ["ordre"])
    op.create_index("ix_niveau_scolaire_cycle_ordre", "niveau_scolaire", ["cycle", "ordre"])

    op.create_table(
        "ecole_niveau_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ecole_id", sa.Integer(), nullable=False),
        sa.Column("niveau_id", sa.Integer(), nullable=False),
        sa.Column("actif", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("date_activation", sa.DateTime(), nullable=True),
        sa.Column("date_desactivation", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["ecole_id"], ["ecole.id"]),
        sa.ForeignKeyConstraint(["niveau_id"], ["niveau_scolaire.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ecole_id", "niveau_id", name="uq_ecole_niveau_config"),
    )
    op.create_index("ix_ecole_niveau_config_ecole_actif", "ecole_niveau_config", ["ecole_id", "actif"])

    with op.batch_alter_table("classe") as batch_op:
        batch_op.add_column(sa.Column("niveau_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("section", sa.String(length=30), nullable=True))
        batch_op.create_foreign_key("fk_classe_niveau_id", "niveau_scolaire", ["niveau_id"], ["id"])
        batch_op.create_index("ix_classe_niveau_id", ["niveau_id"])
        batch_op.create_unique_constraint("uq_classe_ecole_annee_nom", ["ecole_id", "annee_scolaire_id", "nom"])

    niveaux_table = sa.table(
        "niveau_scolaire",
        sa.column("id", sa.Integer),
        sa.column("code", sa.String),
        sa.column("nom", sa.String),
        sa.column("cycle", sa.String),
        sa.column("ordre", sa.Integer),
        sa.column("niveau_suivant_id", sa.Integer),
    )
    op.bulk_insert(niveaux_table, [
        {"id": ordre, "code": code, "nom": nom, "cycle": cycle, "ordre": ordre, "niveau_suivant_id": ordre + 1 if ordre < len(STANDARD_NIVEAUX) else None}
        for code, nom, cycle, ordre in STANDARD_NIVEAUX
    ])

    conn = op.get_bind()
    ecole_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM ecole")).fetchall()]
    if ecole_ids:
        config_table = sa.table(
            "ecole_niveau_config",
            sa.column("ecole_id", sa.Integer),
            sa.column("niveau_id", sa.Integer),
            sa.column("actif", sa.Boolean),
            sa.column("date_activation", sa.DateTime),
        )
        now = datetime.utcnow()
        op.bulk_insert(config_table, [
            {"ecole_id": ecole_id, "niveau_id": ordre, "actif": True, "date_activation": now}
            for ecole_id in ecole_ids
            for _code, _nom, _cycle, ordre in STANDARD_NIVEAUX
        ])


def downgrade():
    with op.batch_alter_table("classe") as batch_op:
        batch_op.drop_constraint("uq_classe_ecole_annee_nom", type_="unique")
        batch_op.drop_index("ix_classe_niveau_id")
        batch_op.drop_constraint("fk_classe_niveau_id", type_="foreignkey")
        batch_op.drop_column("section")
        batch_op.drop_column("niveau_id")

    op.drop_index("ix_ecole_niveau_config_ecole_actif", table_name="ecole_niveau_config")
    op.drop_table("ecole_niveau_config")
    op.drop_index("ix_niveau_scolaire_cycle_ordre", table_name="niveau_scolaire")
    op.drop_index("ix_niveau_scolaire_ordre", table_name="niveau_scolaire")
    op.drop_table("niveau_scolaire")
