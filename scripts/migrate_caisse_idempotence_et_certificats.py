"""Migration idempotente : Clé d'idempotence des paiements et snapshot des certificats.

Exécuter : ``python scripts/migrate_caisse_idempotence_et_certificats.py``.
Compatible SQLite et PostgreSQL.
Préserve l'intégrité de toutes les données existantes.
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

        # 1. Table PAIEMENT
        target_paiement_tables = [t for t in ['paiement', 'paiements'] if t in table_names]
        if target_paiement_tables:
            with db.engine.begin() as conn:
                for table in target_paiement_tables:
                    existing_cols = {c["name"] for c in inspector.get_columns(table)}
                    if "idempotency_key" not in existing_cols:
                        print(f"Table '{table}' : ajout de la colonne 'idempotency_key'...")
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN idempotency_key VARCHAR(64)"))
                        # Création d'index
                        try:
                            conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_idempotency_key ON {table} (idempotency_key)"))
                        except Exception as e:
                            print(f"  Note création index: {e}")
                    else:
                        print(f"Table '{table}' : colonne 'idempotency_key' déjà présente.")

        # 2. Table CERTIFICAT_ADMINISTRATIF
        target_cert_tables = [t for t in ['certificat_administratif', 'certificats_administratifs'] if t in table_names]
        if target_cert_tables:
            cols_cert_to_add = {
                "nom_eleve": "VARCHAR(100)",
                "prenom_eleve": "VARCHAR(100)",
                "matricule_eleve": "VARCHAR(20)",
                "date_naissance_eleve": "DATE",
                "lieu_naissance_eleve": "VARCHAR(100)",
                "nationalite_eleve": "VARCHAR(100)",
                "genre_eleve": "VARCHAR(10)",
                "nom_pere_eleve": "VARCHAR(120)",
                "nom_mere_eleve": "VARCHAR(120)",
                "numero_acte_eleve": "VARCHAR(100)",
            }
            with db.engine.begin() as conn:
                for table in target_cert_tables:
                    existing_cols = {c["name"] for c in inspector.get_columns(table)}
                    for col_name, col_type in cols_cert_to_add.items():
                        if col_name not in existing_cols:
                            print(f"Table '{table}' : ajout de la colonne '{col_name}' ({col_type})...")
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                        else:
                            print(f"Table '{table}' : colonne '{col_name}' déjà présente.")

        print("Migration Idempotence Caisse & Certificats Stricts terminée avec succès.")


if __name__ == "__main__":
    migrate()

