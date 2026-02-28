
import sys
from pathlib import Path

# Ajouter le dossier racine au PYTHONPATH pour que "app.service" soit trouvé
sys.path.append(str(Path(__file__).parent.resolve()))

from app import create_app
from app.extensions import db  # ← Modifier l'import

app = create_app()

if __name__ == '__main__':
    # Crée la base et les tables si elles n'existent pas
    with app.app_context():
        
        db.create_all()
        print("Base de données et tables créées avec succès !")

    # Lancement de l'application
    app.run(debug=True, host='0.0.0.0', port=5000)