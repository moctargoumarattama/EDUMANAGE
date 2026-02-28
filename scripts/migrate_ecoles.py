# scripts/migrate_ecoles.py
def migrer_donnees_existantes():
    """Assigner l'école 1 à toutes les données existantes ayant un ecole_id"""
    from app import create_app, db
    from app.models import Eleve, Professeur, Classe, Cours, Ecole

    app = create_app()
    with app.app_context():
        # Créer une école par défaut si elle n'existe pas
        if not Ecole.query.first():
            ecole = Ecole(nom="École par défaut", adresse="Adresse par défaut")
            db.session.add(ecole)
            db.session.commit()

        ecole_id = 1

        # Liste des modèles à migrer (seulement ceux avec ecole_id)
        modeles_a_migrer = [Eleve, Professeur, Classe]

        for modele in modeles_a_migrer:
            objets = modele.query.filter_by(ecole_id=None).all()
            for obj in objets:
                obj.ecole_id = ecole_id
            print(f"Migré {len(objets)} {modele.__name__}")

        db.session.commit()
        print("Migration terminée")
