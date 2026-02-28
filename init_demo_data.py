from app import create_app, db
from app.models import AnneeScolaire, Classe
from datetime import date, datetime

app = create_app()

with app.app_context():
    # Étape 1 : Vérifier si une année scolaire active existe
    annee_active = AnneeScolaire.query.filter_by(statut="active").first()
    if not annee_active:
        annee_active = AnneeScolaire(
            nom=f"{datetime.now().year}-{datetime.now().year+1}",
            date_debut=date(datetime.now().year, 9, 1),
            date_fin=date(datetime.now().year+1, 7, 31),
            statut="active"
        )
        db.session.add(annee_active)
        db.session.commit()
        print(f"✅ Année scolaire créée : {annee_active.nom}")

    # Étape 2 : Ajouter quelques classes si aucune n’existe
    if Classe.query.count() == 0:
        classes_demo = [
            Classe(nom="6ème A", niveau="Collège", annee_scolaire_id=annee_active.id),
            Classe(nom="5ème B", niveau="Collège", annee_scolaire_id=annee_active.id),
            Classe(nom="Terminale S", niveau="Lycée", annee_scolaire_id=annee_active.id),
        ]
        db.session.add_all(classes_demo)
        db.session.commit()
        print("✅ Classes de test ajoutées avec succès.")
    else:
        print("ℹ️ Des classes existent déjà, aucune insertion effectuée.")

    # Étape 3 : Vérification
    for classe in Classe.query.all():
        print(f"📚 {classe.id} - {classe.nom_complet}")
