from app import db
from app.models import AnneeNiveauConfig, AnneeScolaire, Classe
from app.services.structure_annuelle import (
    get_niveaux_annee,
    get_niveaux_candidats_annuels,
    niveau_est_dans_structure,
    peut_retirer_niveau_structure,
    sauvegarder_structure_annee,
)


def get_selection_annuelle(ecole_id, annee_scolaire_id):
    configs = AnneeNiveauConfig.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_scolaire_id,
    ).all()
    if not configs:
        return None
    return {config.niveau_id for config in configs if config.actif}


def get_niveaux_annuels_actifs(ecole_id, annee_scolaire_id):
    return get_niveaux_annee(ecole_id, annee_scolaire_id)


def niveau_actif_pour_annee(ecole_id, annee_scolaire_id, niveau_id):
    return niveau_est_dans_structure(ecole_id, annee_scolaire_id, niveau_id)


def sauvegarder_selection_annuelle(ecole_id, annee_scolaire_id, niveau_ids):
    res, error = sauvegarder_structure_annee(ecole_id, annee_scolaire_id, niveau_ids)
    if error:
        return None, error
    return {
        "annee_id": res["annee_id"],
        "selected_ids": res["selected_ids"],
        "closed_classes": res.get("closed_classes", 0),
    }, None
