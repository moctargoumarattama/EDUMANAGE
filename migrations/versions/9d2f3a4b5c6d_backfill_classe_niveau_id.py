"""backfill classe niveau id

Revision ID: 9d2f3a4b5c6d
Revises: 8c1d2e3f4a5b
Create Date: 2026-09-10 16:31:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9d2f3a4b5c6d"
down_revision = "8c1d2e3f4a5b"
branch_labels = None
depends_on = None


MAPPINGS = [
    ("CI", "CI"),
    ("CP", "CP"),
    ("CE1", "CE1"),
    ("CE2", "CE2"),
    ("CM1", "CM1"),
    ("CM2", "CM2"),
    ("6E", "6E"),
    ("6EME", "6E"),
    ("6ÈME", "6E"),
    ("5E", "5E"),
    ("5EME", "5E"),
    ("5ÈME", "5E"),
    ("4E", "4E"),
    ("4EME", "4E"),
    ("4ÈME", "4E"),
    ("3E", "3E"),
    ("3EME", "3E"),
    ("3ÈME", "3E"),
    ("2NDE", "2NDE"),
    ("SECONDE", "2NDE"),
    ("1ERE", "1ERE"),
    ("1ÈRE", "1ERE"),
    ("TERMINALE", "TERMINALE"),
    ("TLE", "TERMINALE"),
    ("TERM", "TERMINALE"),
]


def _first_token(value):
    value = (value or "").strip().upper()
    for separator in (" ", "-", "_", "/"):
        value = value.replace(separator, " ")
    return value.split()[0] if value else ""


def upgrade():
    conn = op.get_bind()
    niveau_rows = conn.execute(sa.text("SELECT id, code FROM niveau_scolaire")).fetchall()
    niveau_by_code = {row.code: row.id for row in niveau_rows}
    mapping = {token: niveau_by_code.get(code) for token, code in MAPPINGS}

    classes = conn.execute(sa.text("SELECT id, nom FROM classe WHERE niveau_id IS NULL")).fetchall()
    for classe in classes:
        niveau_id = mapping.get(_first_token(classe.nom))
        if niveau_id:
            conn.execute(
                sa.text("UPDATE classe SET niveau_id = :niveau_id WHERE id = :classe_id"),
                {"niveau_id": niveau_id, "classe_id": classe.id},
            )


def downgrade():
    # On ne retire pas le backfill: cela evite d'effacer une regularisation utile.
    pass
