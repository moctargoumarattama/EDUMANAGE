import sqlite3
import os
import sys
from dotenv import load_dotenv

load_dotenv()

def migrate():
    # Adjust path if needed
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'instance', 'ecole.db')
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        # Try finding in root
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ecole.db')
        if not os.path.exists(db_path):
            print("DB not found at all")
            sys.exit(1)
            
    print(f"Connecting to {db_path}")
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # Auto-commit mode for pragmas
    cursor = conn.cursor()

    try:
        # Disable foreign keys
        cursor.execute("PRAGMA foreign_keys=OFF;")
        
        # Start a transaction for safety
        cursor.execute("BEGIN TRANSACTION;")
        
        # 1. Get current schema of professeur to replicate it (excluding unique on code_prof)
        # Instead of parsing, we can just create the new table explicitly
        cursor.execute('''
        CREATE TABLE professeur_new (
            id INTEGER NOT NULL,
            nom VARCHAR(100) NOT NULL,
            prenom VARCHAR(100) NOT NULL,
            date_naissance DATE,
            adresse VARCHAR(200),
            telephone VARCHAR(20),
            email VARCHAR(120),
            specialite VARCHAR(100),
            matieres_enseignees VARCHAR(200),
            photo VARCHAR(200),
            date_embauche DATETIME,
            planning JSON,
            code_prof VARCHAR(50),
            mot_de_passe VARCHAR(200),
            utilisateur_id INTEGER NOT NULL,
            ecole_id INTEGER NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(ecole_id) REFERENCES ecole (id) ON DELETE CASCADE,
            FOREIGN KEY(utilisateur_id) REFERENCES utilisateur (id) ON DELETE CASCADE,
            UNIQUE (utilisateur_id),
            CONSTRAINT uq_professeur_code_ecole UNIQUE (code_prof, ecole_id)
        );
        ''')

        # 2. Copy data
        cursor.execute('''
        INSERT INTO professeur_new (
            id, nom, prenom, date_naissance, adresse, telephone, email,
            specialite, matieres_enseignees, photo, date_embauche,
            planning, code_prof, mot_de_passe, utilisateur_id, ecole_id
        )
        SELECT 
            id, nom, prenom, date_naissance, adresse, telephone, email,
            specialite, matieres_enseignees, photo, date_embauche,
            planning, code_prof, mot_de_passe, utilisateur_id, ecole_id
        FROM professeur;
        ''')
        
        print(f"Copied {cursor.rowcount} rows to new table.")

        # 3. Drop old table
        cursor.execute("DROP TABLE professeur;")
        
        # 4. Rename new table
        cursor.execute("ALTER TABLE professeur_new RENAME TO professeur;")
        
        # 5. Recreate index
        cursor.execute("CREATE INDEX ix_professeur_utilisateur_ecole ON professeur (utilisateur_id, ecole_id);")

        # Check foreign keys before committing
        cursor.execute("PRAGMA foreign_key_check;")
        fk_violations = cursor.fetchall()
        if fk_violations:
            print("Foreign key violations found! Rolling back.")
            print(fk_violations)
            cursor.execute("ROLLBACK;")
            sys.exit(1)

        cursor.execute("COMMIT;")
        print("Migration successful.")

    except Exception as e:
        cursor.execute("ROLLBACK;")
        print(f"Error during migration: {e}")
        sys.exit(1)
    finally:
        # Re-enable foreign keys
        cursor.execute("PRAGMA foreign_keys=ON;")
        conn.close()

if __name__ == '__main__':
    migrate()
