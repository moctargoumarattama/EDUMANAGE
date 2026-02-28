# populate_db.py
import random
from datetime import datetime
from faker import Faker
from werkzeug.security import generate_password_hash

fake = Faker('fr_FR')

# paramètres configurables
NUM_ECOLES = 10
CLASSES_PAR_ECOLE = 20
NB_PROFS = 500
NB_PARENTS = 800
NB_ELEVES = 800
BATCH_SIZE = 100  # commit par lots

def populate(app=None, db=None):
    """
    Génère des données fictives pour le projet scolaire.
    Usage:
        $ flask shell
        >>> from populate_db import populate
        >>> populate(current_app, db)
    Ou:
        $ python populate_db.py
    """
    if app is None or db is None:
        # Tentative d'import automatique
        try:
            from app import create_app, db as _db
            app = create_app()
            db = _db
        except Exception:
            raise RuntimeError(
                "App et db non fournis. "
                "Appelle populate(app, db) depuis flask shell ou adapte le script."
            )

    with app.app_context():
        # Import des modèles
        from app.models import Ecole, Classe, Utilisateur, Professeur, Eleve

        password_plain = "Password123!"
        password_hash = generate_password_hash(password_plain)

        print("Début génération de données de test...")

        # --- 1) Écoles ---
        ecoles = []
        for i in range(1, NUM_ECOLES + 1):
            nom = f"École Test {i}"
            ecole = Ecole(nom=nom, adresse=fake.address(), telephone=fake.phone_number())
            db.session.add(ecole)
            ecoles.append(ecole)
            if i % BATCH_SIZE == 0:
                db.session.commit()
        db.session.commit()
        print(f"  -> {len(ecoles)} écoles créées")

        # --- 2) Classes par école ---
        classes_by_ecole = {}
        for ecole in ecoles:
            classes = []
            for j in range(1, CLASSES_PAR_ECOLE + 1):
                niveau = random.choice(["6e", "5e", "4e", "3e", "2de", "1re"])
                lettre = chr(64 + (j % 26 or 26))  # A..Z
                nom_classe = f"{niveau.upper()} {lettre}"
                classe = Classe(nom=nom_classe, niveau=niveau, ecole_id=ecole.id)
                db.session.add(classe)
                classes.append(classe)
                if len(classes) % BATCH_SIZE == 0:
                    db.session.commit()
            db.session.commit()
            classes_by_ecole[ecole.id] = Classe.query.filter_by(ecole_id=ecole.id).all()
        total_classes = sum(len(v) for v in classes_by_ecole.values())
        print(f"  -> {total_classes} classes créées ({CLASSES_PAR_ECOLE} par école)")

        # --- 3) Professeurs ---
        profs_created = []
        for i in range(NB_PROFS):
            prenom = fake.first_name()
            nom = fake.last_name()
            email = fake.unique.email()
            tel = fake.phone_number()
            user = Utilisateur(
                nom=nom,
                prenom=prenom,
                email=email,
                mot_de_passe=password_hash,
                role='professeur',
                telephone=tel,
                statut='actif',
                date_creation=datetime.utcnow(),
                ecole_id=random.choice(ecoles).id
            )
            db.session.add(user)
            db.session.flush()  # obtenir user.id

            prof = Professeur(
                utilisateur_id=user.id,
                ecole_id=user.ecole_id,
                prenom=prenom,
                nom=nom,
                email=email,
                telephone=tel,
                specialite=random.choice(['Maths','Français','Histoire','SVT','Anglais','Sport','Physique']),
            )
            db.session.add(prof)
            profs_created.append(prof)

            if (i + 1) % BATCH_SIZE == 0:
                db.session.commit()
        db.session.commit()
        print(f"  -> {len(profs_created)} professeurs créés")

        # --- 4) Parents ---
        parents = []
        for i in range(NB_PARENTS):
            prenom = fake.first_name()
            nom = fake.last_name()
            email = fake.unique.email()
            tel = fake.phone_number()
            user = Utilisateur(
                nom=nom,
                prenom=prenom,
                email=email,
                mot_de_passe=password_hash,
                role='parent',
                telephone=tel,
                statut='actif',
                date_creation=datetime.utcnow(),
                ecole_id=random.choice(ecoles).id
            )
            db.session.add(user)
            parents.append(user)
            if (i + 1) % BATCH_SIZE == 0:
                db.session.commit()
        db.session.commit()
        print(f"  -> {len(parents)} comptes parent créés")

        # --- 5) Élèves ---
        eleves = []
        parent_pool = parents[:] if parents else Utilisateur.query.filter_by(role='parent').all()
        for i in range(NB_ELEVES):
            prenom = fake.first_name()
            nom = fake.last_name()
            ecole = random.choice(ecoles)
            classes = classes_by_ecole.get(ecole.id) or Classe.query.filter_by(ecole_id=ecole.id).all()
            classe = random.choice(classes) if classes else Classe.query.first()
            parent = random.choice(parent_pool)

            eleve = Eleve(
                prenom=prenom,
                nom=nom,
                classe_id=classe.id if classe else None,
                ecole_id=ecole.id,
                parent_id=parent.id,
                email_parent=parent.email,
                telephone=fake.phone_number(),
                date_naissance=fake.date_of_birth(minimum_age=6, maximum_age=18),
                statut='actif'
            )
            db.session.add(eleve)
            eleves.append(eleve)

            if (i + 1) % BATCH_SIZE == 0:
                db.session.commit()
        db.session.commit()
        print(f"  -> {len(eleves)} élèves créés")

        # --- Résumé ---
        print("Population terminée.")
        print(f"Écoles: {len(ecoles)}, Classes: {total_classes}, Profs: {len(profs_created)}, Parents: {len(parents)}, Élèves: {len(eleves)}")
        print(f"Comptes de test ont le mot de passe: {password_plain}")

if __name__ == "__main__":
    try:
        from app import create_app, db
        app = create_app()
        populate(app, db)
    except Exception as e:
        print("Erreur lors de l'exécution standalone :", e)
        print("Lancer depuis flask shell est recommandé :")
        print("  $ flask shell")
        print("  >>> from populate_db import populate; populate(current_app, db)")
