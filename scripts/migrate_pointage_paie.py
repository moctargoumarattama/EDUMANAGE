import os
import sys
import sqlite3

# Assurer que la racine du projet est dans sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.models import PointagePersonnel, FichePaiePersonnel, Professeur

def sync_schema():
    app = create_app()
    with app.app_context():
        db_path = os.path.join(app.instance_path, 'ecole.db')
        print(f"Vérification de la base de données : {db_path}")

        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            # 1. Vérification et ajout des colonnes à la table professeur
            cur.execute("PRAGMA table_info(professeur)")
            existing_cols = [row[1] for row in cur.fetchall()]
            print(f"Colonnes actuelles de 'professeur': {len(existing_cols)}")

            if 'type_remuneration' not in existing_cols:
                print("-> Ajout de la colonne 'type_remuneration' à professeur...")
                cur.execute("ALTER TABLE professeur ADD COLUMN type_remuneration VARCHAR(20) DEFAULT 'fixe'")

            if 'salaire_base' not in existing_cols:
                print("-> Ajout de la colonne 'salaire_base' à professeur...")
                cur.execute("ALTER TABLE professeur ADD COLUMN salaire_base FLOAT DEFAULT 0.0")

            if 'taux_horaire' not in existing_cols:
                print("-> Ajout de la colonne 'taux_horaire' à professeur...")
                cur.execute("ALTER TABLE professeur ADD COLUMN taux_horaire FLOAT DEFAULT 0.0")

            conn.commit()
            conn.close()

        # 2. Création des nouvelles tables PointagePersonnel et FichePaiePersonnel
        db.create_all()
        print("db.create_all() exécuté avec succès.")

        # 3. Vérification des nouvelles tables créées
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('pointage_personnel', 'fiche_paie_personnel')")
            tables = [row[0] for row in cur.fetchall()]
            print(f"Tables détectées : {tables}")

            for t in ['pointage_personnel', 'fiche_paie_personnel']:
                cur.execute(f"PRAGMA table_info({t})")
                cols = [row[1] for row in cur.fetchall()]
                print(f"Colonnes de '{t}' ({len(cols)}): {cols}")

            conn.close()

if __name__ == '__main__':
    sync_schema()
