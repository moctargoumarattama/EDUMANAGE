"""Service canonique pour la gestion de la structure annuelle des niveaux scolaires.

Règle métier fondamentale :
Pour une école et une année scolaire données, la SEULE source de vérité
indiquant quels niveaux existent réellement dans le système est :
AnneeNiveauConfig.
"""

from app import db
from app.models import (
    Absence,
    AnneeNiveauConfig,
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    EmploiTemps,
    Inscription,
    NiveauScolaire,
    Note,
    Paiement,
)


def get_niveaux_annee(ecole_id: int, annee_scolaire_id: int):
    """
    Source canonique UNIQUE des niveaux de l'année scolaire.
    Lit UNIQUEMENT AnneeNiveauConfig où actif=True.
    Retourne la liste ordonnée des NiveauScolaire.
    """
    if not ecole_id or not annee_scolaire_id:
        return []
    return (
        NiveauScolaire.query
        .join(AnneeNiveauConfig, AnneeNiveauConfig.niveau_id == NiveauScolaire.id)
        .filter(
            AnneeNiveauConfig.ecole_id == ecole_id,
            AnneeNiveauConfig.annee_scolaire_id == annee_scolaire_id,
            AnneeNiveauConfig.actif.is_(True),
        )
        .order_by(NiveauScolaire.ordre.asc())
        .all()
    )


def niveau_est_dans_structure(ecole_id: int, annee_scolaire_id: int, niveau_id: int) -> bool:
    """
    Vérifie si un niveau est actif dans la structure de l'année scolaire.
    Source UNIQUE de vérité : AnneeNiveauConfig(actif=True).
    Retourne strictement True si le niveau est actif pour l'école et l'année, False sinon.
    """
    if not ecole_id or not annee_scolaire_id or not niveau_id:
        return False

    return AnneeNiveauConfig.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_scolaire_id,
        niveau_id=niveau_id,
        actif=True,
    ).first() is not None


def get_classes_niveau_annee(ecole_id: int, annee_scolaire_id: int, niveau_id: int, statut: str = None):
    """Retourne les classes associées à un niveau pour une école et une année données."""
    if not ecole_id or not annee_scolaire_id or not niveau_id:
        return []
    query = Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_scolaire_id,
        niveau_id=niveau_id,
    )
    if statut:
        query = query.filter_by(statut=statut)
    return query.order_by(Classe.nom.asc()).all()


def peut_retirer_niveau_structure(ecole_id: int, annee_scolaire_id: int, niveau_id: int):
    """
    Vérifie si un niveau peut être retiré de la structure annuelle.
    Retourne (True, None) si le niveau n'est pas utilisé dans cette année.
    Retourne (False, message) s'il possède déjà des classes ou données dépendantes.
    """
    niveau = db.session.get(NiveauScolaire, niveau_id)
    niveau_nom = niveau.nom if niveau else f"ID {niveau_id}"

    classes = Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_scolaire_id,
        niveau_id=niveau_id,
    ).all()

    if not classes:
        return True, None

    class_ids = [c.id for c in classes]

    # Vérification des inscriptions
    has_inscr = Inscription.query.filter(
        Inscription.ecole_id == ecole_id,
        Inscription.annee_scolaire_id == annee_scolaire_id,
        Inscription.classe_id.in_(class_ids),
    ).first() is not None
    if has_inscr:
        return False, f"Ce niveau possède déjà des classes avec élèves inscrits dans cette année scolaire et ne peut pas être retiré."

    # Vérification des cours
    has_cours = Cours.query.filter(
        Cours.ecole_id == ecole_id,
        Cours.classe_id.in_(class_ids),
    ).first() is not None
    if has_cours:
        return False, f"Ce niveau possède déjà des classes avec cours rattachés dans cette année scolaire et ne peut pas être retiré."

    # Vérification des emplois du temps
    has_edt = EmploiTemps.query.filter(
        EmploiTemps.ecole_id == ecole_id,
        EmploiTemps.classe_id.in_(class_ids),
    ).first() is not None
    if has_edt:
        return False, f"Ce niveau possède déjà des classes avec créneaux d'emploi du temps dans cette année scolaire et ne peut pas être retiré."

    return False, "Ce niveau possède déjà des classes dans cette année scolaire et ne peut pas être retiré."


def get_niveaux_candidats_annuels(ecole_id: int = None):
    """
    Retourne l'ensemble des niveaux du catalogue technique NiveauScolaire
    disponibles pour être configurés dans la structure annuelle d'un établissement.
    NiveauScolaire fournit TOUS les niveaux possibles sans restriction d'EcoleNiveauConfig.
    """
    return NiveauScolaire.query.order_by(NiveauScolaire.ordre.asc()).all()


def get_niveaux_catalogue_grouped_for_onboarding(ecole_id: int, annee_scolaire_id: int = None):
    """
    Fournit les niveaux pour l'étape pédagogique de l'onboarding.
    Lit directement depuis NiveauScolaire (catalogue technique).
    Marque actif=True si déjà présent dans AnneeNiveauConfig pour cette année.
    Zéro dépendance à EcoleNiveauConfig.
    """
    from types import SimpleNamespace

    active_ids = set()
    if annee_scolaire_id:
        active_ids = {n.id for n in get_niveaux_annee(ecole_id, annee_scolaire_id)}

    niveaux = NiveauScolaire.query.order_by(NiveauScolaire.ordre.asc()).all()
    grouped = {"primaire": [], "college": [], "lycee": []}
    for niveau in niveaux:
        grouped.setdefault(niveau.cycle, []).append(SimpleNamespace(
            niveau_id=niveau.id,
            actif=niveau.id in active_ids,
            niveau=niveau,
        ))
    return grouped


def sauvegarder_structure_annee(ecole_id: int, annee_scolaire_id: int, niveau_ids: list):
    """
    Enregistre la structure annuelle (AnneeNiveauConfig).
    Valide que l'année n'est pas archivée et qu'aucun niveau utilisé n'est retiré.
    AnneeNiveauConfig contient UNIQUEMENT la sélection enregistrée.
    """
    annee = AnneeScolaire.query.filter_by(id=annee_scolaire_id, ecole_id=ecole_id).first()
    if not annee:
        return None, "Année scolaire invalide pour cet établissement."
    if annee.statut == "archivee":
        return None, "Impossible de modifier la structure d'une année archivée."

    candidats = get_niveaux_candidats_annuels(ecole_id)
    allowed_ids = {n.id for n in candidats}
    selected_ids = {int(nid) for nid in (niveau_ids or []) if str(nid).isdigit()}

    # Protection contre les identifiants de niveaux invalides
    if not selected_ids.issubset(allowed_ids):
        return None, "Un niveau sélectionné est invalide."

    existing = {
        config.niveau_id: config
        for config in AnneeNiveauConfig.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id,
        ).all()
    }

    # Protection d'intégrité : interdire le retrait d'un niveau déjà utilisé
    for niveau_id, config in list(existing.items()):
        if config.actif and (niveau_id not in selected_ids):
            peut_retirer, raison = peut_retirer_niveau_structure(ecole_id, annee.id, niveau_id)
            if not peut_retirer:
                return None, raison
            db.session.delete(config)

    # Mise à jour ou création des configurations annuelles uniquement pour les niveaux sélectionnés
    for niveau_id in selected_ids:
        config = existing.get(niveau_id)
        if not config:
            config = AnneeNiveauConfig(
                ecole_id=ecole_id,
                annee_scolaire_id=annee.id,
                niveau_id=niveau_id,
                actif=True,
            )
            db.session.add(config)
        else:
            config.actif = True

    db.session.flush()
    return {
        "annee_id": annee.id,
        "selected_ids": selected_ids,
        "closed_classes": 0,
    }, None


def copier_structure_annee(ecole_id: int, annee_source_id: int, annee_cible_id: int):
    """
    Copie la sélection de niveaux AnneeNiveauConfig de l'année source vers l'année cible.
    Une fois copiées, les deux structures restent indépendantes.
    """
    source_configs = AnneeNiveauConfig.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_source_id,
        actif=True,
    ).all()

    if not source_configs:
        return False, "Aucune structure active trouvée pour l'année source."

    niveau_ids = [c.niveau_id for c in source_configs]
    return sauvegarder_structure_annee(ecole_id, annee_cible_id, niveau_ids)


def verifier_integrite_classes(ecole_id: int = None):
    """
    Contrôle d'intégrité : détecte les classes qui n'ont pas d'AnneeNiveauConfig actif correspondant.
    Retourne la liste des classes hors structure.
    """
    query = Classe.query
    if ecole_id:
        query = query.filter_by(ecole_id=ecole_id)
    classes = query.all()

    hors_structure = []
    for c in classes:
        cfg = AnneeNiveauConfig.query.filter_by(
            ecole_id=c.ecole_id,
            annee_scolaire_id=c.annee_scolaire_id,
            niveau_id=c.niveau_id,
            actif=True,
        ).first()
        if not cfg:
            hors_structure.append(c)

    return hors_structure

