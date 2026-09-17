from app import db
from app.models import AnneeScolaire, Classe, Inscription
from app.services.structure_annuelle import niveau_est_dans_structure


STATUT_CLASSE_OUVERTE = "ouverte"
STATUT_CLASSE_FERMEE = "fermee"
STATUTS_CLASSE = {STATUT_CLASSE_OUVERTE, STATUT_CLASSE_FERMEE}


def classe_est_ouverte(classe):
    return bool(classe) and (classe.statut or STATUT_CLASSE_OUVERTE) == STATUT_CLASSE_OUVERTE


def get_classes_ouvertes_annee(ecole_id, annee_scolaire_id):
    return Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_scolaire_id,
        statut=STATUT_CLASSE_OUVERTE,
    )


def set_classe_ouverte(ecole_id, classe_id, ouverte):
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe introuvable pour cet etablissement."
    if classe.annee_scolaire and classe.annee_scolaire.statut == "archivee":
        return None, "Impossible de modifier une classe d'une annee archivee."

    classe.statut = STATUT_CLASSE_OUVERTE if ouverte else STATUT_CLASSE_FERMEE
    db.session.flush()
    return classe, None


def _classe_identity(classe):
    section = (classe.section or "").strip().upper()
    if classe.niveau_id:
        return ("niveau_section", classe.niveau_id, section)
    return ("nom", (classe.nom or "").strip().upper())


def _source_classes_autorisees(ecole_id, source_annee_id):
    return (
        Classe.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=source_annee_id)
        .order_by(Classe.niveau_id.asc(), Classe.nom.asc(), Classe.id.asc())
        .all()
    )


def _resolve_source_annee(ecole_id, annee_cible, source_annee_id=None):
    if source_annee_id is not None:
        source = AnneeScolaire.query.filter_by(id=source_annee_id, ecole_id=ecole_id).first()
        if not source:
            return None, "Annee source invalide pour cet etablissement."
    else:
        source = (
            AnneeScolaire.query
            .filter(
                AnneeScolaire.ecole_id == ecole_id,
                AnneeScolaire.id != annee_cible.id,
                AnneeScolaire.date_debut < annee_cible.date_debut,
            )
            .order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc())
            .first()
        )

    if source and source.id == annee_cible.id:
        return None, "L'annee source doit etre differente de l'annee cible."
    return source, None


def preparer_structure_classes(ecole_id, annee_cible_id, source_annee_id=None, statuts=None):
    annee_cible = AnneeScolaire.query.filter_by(id=annee_cible_id, ecole_id=ecole_id).first()
    if not annee_cible:
        return None, "Annee cible invalide pour cet etablissement."
    if annee_cible.statut == "archivee":
        return None, "Impossible de preparer les classes d'une annee archivee."

    source, error = _resolve_source_annee(ecole_id, annee_cible, source_annee_id)
    if error:
        return None, error
    if not source:
        return {"created": [], "existing": [], "skipped": [], "source_to_target": {}, "source": None, "target": annee_cible}, None

    existing = {
        _classe_identity(classe): classe
        for classe in Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_cible.id).all()
    }
    requested_statuts = statuts or {}
    result = {"created": [], "existing": [], "skipped": [], "source_to_target": {}, "source": source, "target": annee_cible}

    for source_classe in _source_classes_autorisees(ecole_id, source.id):
        if not source_classe.niveau_id or not niveau_est_dans_structure(ecole_id, annee_cible.id, source_classe.niveau_id):
            result["skipped"].append(source_classe)
            continue

        identity = _classe_identity(source_classe)
        requested = requested_statuts.get(source_classe.id, source_classe.statut or STATUT_CLASSE_OUVERTE)
        statut = requested if requested in STATUTS_CLASSE else STATUT_CLASSE_OUVERTE

        if identity in existing:
            target = existing[identity]
            result["existing"].append(target)
            result["source_to_target"][source_classe.id] = target
            continue

        classe = Classe(
            nom=source_classe.nom,
            niveau=source_classe.niveau,
            niveau_id=source_classe.niveau_id,
            section=source_classe.section,
            effectif=0,
            capacite=source_classe.capacite or source_classe.capacite_max or 35,
            capacite_max=source_classe.capacite_max or source_classe.capacite or 35,
            statut=statut,
            ecole_id=ecole_id,
            salle=source_classe.salle,
            professeur_id=None,
            annee_scolaire_id=annee_cible.id,
        )
        db.session.add(classe)
        db.session.flush()
        existing[identity] = classe
        result["created"].append(classe)
        result["source_to_target"][source_classe.id] = classe

    return result, None


def preparer_structure_annee(ecole_id, annee_cible_id, annee_source_id=None):
    classes_result, error = preparer_structure_classes(ecole_id, annee_cible_id, annee_source_id)
    if error:
        return None, error

    from app.services.cours_annuels import preparer_cours_pour_correspondances

    cours_result = preparer_cours_pour_correspondances(
        ecole_id=ecole_id,
        source_to_target=classes_result["source_to_target"],
    )
    summary = {
        "annee_source_id": classes_result["source"].id if classes_result["source"] else None,
        "annee_cible_id": classes_result["target"].id,
        "classes_creees": len(classes_result["created"]),
        "classes_existantes": len(classes_result["existing"]),
        "classes_ignorees_niveau_desactive": len(classes_result["skipped"]),
        "classes_fermees": sum(1 for classe in list(classes_result["created"]) + list(classes_result["existing"]) if not classe_est_ouverte(classe)),
        "cours_crees": len(cours_result["created"]),
        "cours_existants": len(cours_result["existing"]),
        "cours_ignores_classe_fermee": len(cours_result["skipped_closed_class"]),
        "classes": classes_result,
        "cours": cours_result,
    }
    return summary, None


def count_inscriptions_annee(ecole_id, annee_scolaire_id):
    return Inscription.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_scolaire_id).count()


def precharger_effectifs_classes(classes, ecole_id=None, annee_id=None):
    """
    Précharge en lot les effectifs réels d'une liste de classes via Inscription.
    Exécute UNE SEULE requête d'agrégation GROUP BY.
    Injecte c._effectif_annuel sur chaque instance de classe.
    Garantit une isolation stricte par école, par année scolaire et par statut actif.
    """
    if not classes:
        return
    classes_list = list(classes)
    classe_ids = [c.id for c in classes_list if c and getattr(c, 'id', None)]
    if not classe_ids:
        for c in classes_list:
            if c:
                c._effectif_annuel = 0
        return

    query = (
        db.session.query(
            Inscription.classe_id,
            Inscription.annee_scolaire_id,
            db.func.count(Inscription.id)
        )
        .filter(
            Inscription.classe_id.in_(classe_ids),
            db.or_(Inscription.statut != 'desinscrit', Inscription.statut.is_(None))
        )
    )
    if ecole_id:
        query = query.filter(Inscription.ecole_id == ecole_id)
    if annee_id:
        query = query.filter(Inscription.annee_scolaire_id == annee_id)

    counts = query.group_by(Inscription.classe_id, Inscription.annee_scolaire_id).all()
    effectifs_map = {(row[0], row[1]): row[2] for row in counts}

    for c in classes_list:
        if c:
            c._effectif_annuel = effectifs_map.get((c.id, c.annee_scolaire_id), 0)


def precharger_effectif_classe(classe):
    """
    Précharge explicitement l'effectif réel d'une seule classe via Inscription.
    Exécute UNE SEULE requête ciblée et injecte classe._effectif_annuel.
    """
    if not classe or not getattr(classe, 'id', None):
        if classe:
            classe._effectif_annuel = 0
        return 0

    count = (
        Inscription.query
        .filter(
            Inscription.ecole_id == classe.ecole_id,
            Inscription.annee_scolaire_id == classe.annee_scolaire_id,
            Inscription.classe_id == classe.id,
            db.or_(Inscription.statut != 'desinscrit', Inscription.statut.is_(None))
        )
        .count()
    )
    classe._effectif_annuel = count
    return count
