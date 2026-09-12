from app import db
from app.models import AnneeNiveauConfig, AnneeScolaire, Classe
from app.services.niveaux import get_niveaux_actifs


def get_niveaux_candidats_annuels(ecole_id):
    return get_niveaux_actifs(ecole_id)


def get_selection_annuelle(ecole_id, annee_scolaire_id):
    configs = AnneeNiveauConfig.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_scolaire_id,
    ).all()
    if not configs:
        return None
    return {config.niveau_id for config in configs if config.actif}


def get_niveaux_annuels_actifs(ecole_id, annee_scolaire_id):
    candidats = get_niveaux_candidats_annuels(ecole_id)
    selection = get_selection_annuelle(ecole_id, annee_scolaire_id)
    if selection is None:
        return candidats
    return [niveau for niveau in candidats if niveau.id in selection]


def niveau_actif_pour_annee(ecole_id, annee_scolaire_id, niveau_id):
    selection = get_selection_annuelle(ecole_id, annee_scolaire_id)
    if selection is None:
        return any(niveau.id == niveau_id for niveau in get_niveaux_candidats_annuels(ecole_id))
    return niveau_id in selection


def sauvegarder_selection_annuelle(ecole_id, annee_scolaire_id, niveau_ids):
    annee = AnneeScolaire.query.filter_by(id=annee_scolaire_id, ecole_id=ecole_id).first()
    if not annee:
        return None, "Annee scolaire invalide pour cet etablissement."
    if annee.statut == "archivee":
        return None, "Impossible de modifier la structure d'une annee archivee."

    candidats = get_niveaux_candidats_annuels(ecole_id)
    allowed_ids = {niveau.id for niveau in candidats}
    selected_ids = {int(niveau_id) for niveau_id in (niveau_ids or []) if str(niveau_id).isdigit()}
    if not selected_ids.issubset(allowed_ids):
        return None, "Un niveau selectionne n'est pas autorise pour cet etablissement."

    existing = {
        config.niveau_id: config
        for config in AnneeNiveauConfig.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id,
        ).all()
    }

    for niveau in candidats:
        config = existing.get(niveau.id)
        if not config:
            config = AnneeNiveauConfig(
                ecole_id=ecole_id,
                annee_scolaire_id=annee.id,
                niveau_id=niveau.id,
            )
            db.session.add(config)
        config.actif = niveau.id in selected_ids

    closed_count = 0
    deselected_ids = allowed_ids - selected_ids
    if deselected_ids:
        classes = Classe.query.filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee.id,
            Classe.niveau_id.in_(deselected_ids),
        ).all()
        for classe in classes:
            if classe.statut != "fermee":
                classe.statut = "fermee"
                closed_count += 1

    db.session.flush()
    return {
        "annee_id": annee.id,
        "selected_ids": selected_ids,
        "closed_classes": closed_count,
    }, None
