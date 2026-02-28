from flask import current_app


def init_ecoles_par_defaut(db):
    from app.models import Ecole

    if not Ecole.query.first():
        ecole1 = Ecole(
            nom="École Primaire A",
            adresse="Adresse A",
            email="contact@ecolea.ne",
            telephone="+22700000000",
            directeur="Directeur A"
        )
        ecole2 = Ecole(
            nom="Collège B",
            adresse="Adresse B",
            email="contact@collegeb.ne",
            telephone="+22700000001",
            directeur="Directeur B"
        )
        db.session.add_all([ecole1, ecole2])
        db.session.commit()
        
        # Assigner toutes les données existantes à la première école
        assigner_donnees_existantes(db)

def assigner_donnees_existantes(db):
    """Assigner les élèves/profs/classes/cours à une école par défaut si ecole_id est NULL"""
    from app.models import Ecole, Eleve, Professeur, Classe, Cours

    ecole_defaut = Ecole.query.first()
    if not ecole_defaut:
        return

    for modele in [Eleve, Professeur, Classe, Cours]:
        objets = modele.query.filter_by(ecole_id=None).all()
        for obj in objets:
            obj.ecole_id = ecole_defaut.id

    db.session.commit()
