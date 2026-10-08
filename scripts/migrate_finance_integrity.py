"""Migration idempotente des champs financiers d'inscription.

Exécuter après sauvegarde : ``python scripts/migrate_finance_integrity.py``.
La migration n'écrase aucune donnée existante.
"""
import os
import sys

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import create_app, db


def migrate():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        columns = {c["name"] for c in inspector.get_columns("inscriptions")}
        types = {
            "frais_scolarite": "FLOAT",
            "remise": "FLOAT NOT NULL DEFAULT 0",
            "frais_inscription": "FLOAT NOT NULL DEFAULT 0",
        }
        with db.engine.begin() as conn:
            for name, sql_type in types.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE inscriptions ADD COLUMN {name} {sql_type}"))
        current_inspector = inspect(db.engine)
        indexes = {i["name"] for i in current_inspector.get_indexes("paiement")}
        constraints = {c["name"] for c in current_inspector.get_unique_constraints("paiement")}
        if "uq_paiement_ecole_reference" not in indexes and "uq_paiement_ecole_reference" not in constraints:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX uq_paiement_ecole_reference "
                        "ON paiement (ecole_id, reference)"
                    ))
            except IntegrityError:
                print("AVERTISSEMENT: références historiques dupliquées; index unique non créé.")
                print("Les nouveaux doublons restent refusés par le service applicatif.")
        print("Migration financière appliquée.")


if __name__ == "__main__":
    migrate()
