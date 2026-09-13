from flask import current_app


def init_ecoles_par_defaut(db):
    """
    [DÉPRÉCIÉ] Dans une installation propre de KLASORA, aucune école par défaut
    ne doit être créée automatiquement.
    Les écoles sont créées par les utilisateurs via l'onboarding.
    """
    current_app.logger.warning("[init_ecoles] init_ecoles_par_defaut() est désactivé : aucune école fictive ne sera créée.")
    return


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
