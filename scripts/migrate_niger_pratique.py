"""Migration idempotente des champs d'état civil Niger pour les élèves.

Exécuter : ``python scripts/migrate_niger_pratique.py``.
Compatible SQLite et PostgreSQL.
Préserve l'intégrité de toutes les données existantes (matricules, codes parents, etc.).
"""
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import create_app, db


def migrate(app=None):
    if app is None:
        app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        table_names = inspector.get_table_names()

        target_tables = [t for t in ['eleve', 'eleves'] if t in table_names]
        if not target_tables:
            print("Aucune table 'eleve' ou 'eleves' trouvée.")
            return

        cols_to_add = {
            "nationalite": "VARCHAR(60) DEFAULT 'Nigérienne'",
            "numero_acte": "VARCHAR(100)",
            "nom_pere": "VARCHAR(150)",
            "nom_mere": "VARCHAR(150)",
        }

        with db.engine.begin() as conn:
            for table in target_tables:
                existing_cols = {c["name"] for c in inspector.get_columns(table)}
                print(f"Table '{table}' : {len(existing_cols)} colonnes existantes.")

                added_count = 0
                for col_name, col_type in cols_to_add.items():
                    if col_name not in existing_cols:
                        print(f"  -> Ajout de la colonne '{col_name}' ({col_type})...")
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                        added_count += 1
                    else:
                        print(f"  -> Colonne '{col_name}' déjà présente.")

                # Renseigner la nationalité par défaut pour les dossiers existants
                res = conn.execute(text(
                    f"UPDATE {table} SET nationalite = 'Nigérienne' "
                    f"WHERE nationalite IS NULL OR TRIM(nationalite) = ''"
                ))
                updated_rows = res.rowcount if hasattr(res, "rowcount") and res.rowcount is not None and res.rowcount >= 0 else 0
                print(f"  -> {updated_rows} élève(s) mis à jour avec nationalité 'Nigérienne' par défaut.")

        print("Migration État Civil Niger terminée avec succès.")


if __name__ == "__main__":
    migrate()
