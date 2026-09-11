"""phase2a inscriptions annuelles

Revision ID: a7e4c2d9f1b0
Revises: 9d2f3a4b5c6d
Create Date: 2026-09-11 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime


revision = "a7e4c2d9f1b0"
down_revision = "9d2f3a4b5c6d"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    now = datetime.utcnow()

    with op.batch_alter_table("inscriptions") as batch_op:
        batch_op.add_column(sa.Column("ecole_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("statut", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("date_inscription", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("date_sortie", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("motif_sortie", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("decision_fin_annee", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))

    conn.execute(sa.text("""
        UPDATE inscriptions
        SET ecole_id = (
            SELECT eleve.ecole_id FROM eleve WHERE eleve.id = inscriptions.eleve_id
        )
        WHERE ecole_id IS NULL
    """))
    conn.execute(sa.text("UPDATE inscriptions SET statut = 'inscrit' WHERE statut IS NULL"))
    for column in ("date_inscription", "created_at", "updated_at"):
        conn.execute(
            sa.text(f"UPDATE inscriptions SET {column} = :now WHERE {column} IS NULL"),
            {"now": now},
        )

    eleves = conn.execute(sa.text("""
        SELECT eleve.id AS eleve_id,
               eleve.ecole_id AS ecole_id,
               eleve.classe_id AS classe_id,
               classe.annee_scolaire_id AS annee_scolaire_id
        FROM eleve
        JOIN classe ON classe.id = eleve.classe_id
        WHERE eleve.classe_id IS NOT NULL
          AND classe.ecole_id = eleve.ecole_id
          AND classe.annee_scolaire_id IS NOT NULL
    """)).fetchall()

    for row in eleves:
        exists = conn.execute(sa.text("""
            SELECT id FROM inscriptions
            WHERE ecole_id = :ecole_id
              AND eleve_id = :eleve_id
              AND annee_scolaire_id = :annee_scolaire_id
            LIMIT 1
        """), {
            "ecole_id": row.ecole_id,
            "eleve_id": row.eleve_id,
            "annee_scolaire_id": row.annee_scolaire_id,
        }).fetchone()
        if exists:
            continue
        conn.execute(sa.text("""
            INSERT INTO inscriptions (
                ecole_id, eleve_id, classe_id, cours_id, annee_scolaire_id,
                statut, date_inscription, created_at, updated_at
            )
            VALUES (
                :ecole_id, :eleve_id, :classe_id, NULL, :annee_scolaire_id,
                'inscrit', :now, :now, :now
            )
        """), {
            "ecole_id": row.ecole_id,
            "eleve_id": row.eleve_id,
            "classe_id": row.classe_id,
            "annee_scolaire_id": row.annee_scolaire_id,
            "now": now,
        })

    with op.batch_alter_table("inscriptions") as batch_op:
        batch_op.alter_column("ecole_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("statut", existing_type=sa.String(length=20), nullable=False)
        batch_op.alter_column("date_inscription", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column("updated_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.create_foreign_key("fk_inscriptions_ecole_id", "ecole", ["ecole_id"], ["id"])
        batch_op.create_unique_constraint(
            "uq_inscription_ecole_annee_eleve",
            ["ecole_id", "annee_scolaire_id", "eleve_id"],
        )
        batch_op.create_index("ix_inscription_ecole_annee", ["ecole_id", "annee_scolaire_id"])
        batch_op.create_index("ix_inscription_eleve_annee", ["eleve_id", "annee_scolaire_id"])
        batch_op.create_index("ix_inscription_classe", ["classe_id"])


def downgrade():
    with op.batch_alter_table("inscriptions") as batch_op:
        batch_op.drop_index("ix_inscription_classe")
        batch_op.drop_index("ix_inscription_eleve_annee")
        batch_op.drop_index("ix_inscription_ecole_annee")
        batch_op.drop_constraint("uq_inscription_ecole_annee_eleve", type_="unique")
        batch_op.drop_constraint("fk_inscriptions_ecole_id", type_="foreignkey")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("decision_fin_annee")
        batch_op.drop_column("motif_sortie")
        batch_op.drop_column("date_sortie")
        batch_op.drop_column("date_inscription")
        batch_op.drop_column("statut")
        batch_op.drop_column("ecole_id")
