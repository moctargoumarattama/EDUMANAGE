from flask import g, has_request_context
from sqlalchemy import case, func, or_
from sqlalchemy.orm import joinedload, selectinload

from app.models import Absence, AnneeScolaire, Classe, Cours, Eleve, Inscription, db
from app.utils_classes import classes_triees_pedagogique


MESSAGE_ANNEE_PLANIFIEE = "Les absences pourront être saisies lorsque cette année sera active."
MESSAGE_ANNEE_ARCHIVEE = "Cette année est archivée : les absences sont consultables en lecture seule."


def _is_admin_like(user):
    return getattr(user, "role", None) in {"admin", "super_admin"}


def _professeur(user):
    return getattr(user, "professeur_rel", None)


def _professeur_classe_ids(user, annee_id=None):
    professeur = _professeur(user)
    if not professeur:
        return set()

    cache_key = f"_prof_classe_ids_{professeur.id}_{annee_id}"
    if has_request_context() and hasattr(g, cache_key):
        return getattr(g, cache_key)

    query = (
        Cours.query.with_entities(Cours.classe_id)
        .join(Classe, Classe.id == Cours.classe_id)
        .filter(
            Cours.professeur_id == professeur.id,
            Cours.ecole_id == professeur.ecole_id,
            Cours.classe_id.isnot(None),
            Classe.ecole_id == professeur.ecole_id,
        )
    )
    if annee_id:
        query = query.filter(Classe.annee_scolaire_id == annee_id)
    res = {row.classe_id for row in query.all()}

    if has_request_context():
        setattr(g, cache_key, res)
    return res


def _parent_enfant_ids(user):
    enfants = getattr(user, "enfants", None)
    if not enfants:
        return set()
    if hasattr(enfants, "all"):
        enfants = enfants.all()
    return {enfant.id for enfant in enfants}


def statut_annee_absences(annee):
    if not annee:
        return "Aucune année scolaire active ou consultée."
    if annee.statut == "planifiee":
        return MESSAGE_ANNEE_PLANIFIEE
    if annee.statut == "archivee":
        return MESSAGE_ANNEE_ARCHIVEE
    return None


def absences_modifiables(annee, user):
    return bool(annee and annee.statut == "active" and getattr(user, "role", None) in {"admin", "professeur"})


def get_inscription_eleve_annee(ecole_id, eleve_id, annee_id):
    if not all([ecole_id, eleve_id, annee_id]):
        return None
    return (
        Inscription.query.options(
            selectinload(Inscription.eleve),
            selectinload(Inscription.classe),
        )
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.eleve_id == eleve_id,
            Inscription.annee_scolaire_id == annee_id,
        )
        .first()
    )


def get_inscriptions_absences(ecole_id, annee, user):
    if not ecole_id or not annee:
        return []

    query = (
        Inscription.query.options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
        )
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
            Eleve.ecole_id == ecole_id,
        )
    )

    if getattr(user, "role", None) == "parent":
        ids = _parent_enfant_ids(user)
        query = query.filter(Inscription.eleve_id.in_(ids)) if ids else query.filter(False)
    elif getattr(user, "role", None) == "professeur":
        classe_ids = _professeur_classe_ids(user, annee.id)
        query = query.filter(Inscription.classe_id.in_(classe_ids)) if classe_ids else query.filter(False)

    inscriptions = query.all()
    inscriptions.sort(key=lambda ins: ((ins.classe.nom if ins.classe else ""), ins.eleve.nom, ins.eleve.prenom))
    return inscriptions


def get_eleves_choices_absences(ecole_id, annee, user):
    inscriptions = get_inscriptions_absences(ecole_id, annee, user)
    return [
        (ins.eleve.id, f"{ins.eleve.prenom} {ins.eleve.nom} - {ins.classe.nom if ins.classe else 'Sans classe'}")
        for ins in inscriptions
        if ins.eleve
    ]


def get_classes_absences(ecole_id, annee, user):
    """Retourne les classes visibles dans le module absences selon le role."""
    if not ecole_id or not annee:
        return []

    query = Classe.query.options(joinedload(Classe.niveau_scolaire)).filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
    if getattr(user, "role", None) == "professeur":
        classe_ids = _professeur_classe_ids(user, annee.id)
        if not classe_ids:
            return []
        query = query.filter(Classe.id.in_(classe_ids))

    return classes_triees_pedagogique(query).all()


def get_cours_absences(ecole_id, annee, user, classe_ids=None):
    if not ecole_id or not annee:
        return []

    query = (
        Cours.query.options(selectinload(Cours.classe))
        .join(Classe, Classe.id == Cours.classe_id)
        .filter(
            Cours.ecole_id == ecole_id,
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee.id,
        )
    )
    if classe_ids:
        query = query.filter(Cours.classe_id.in_(classe_ids))
    if getattr(user, "role", None) == "professeur":
        professeur = _professeur(user)
        query = query.filter(Cours.professeur_id == professeur.id) if professeur else query.filter(False)

    return query.order_by(Cours.nom.asc(), Cours.id.asc()).all()


def get_cours_choices_absences(ecole_id, annee, user):
    return [(cours.id, f"{cours.nom} - {cours.classe.nom if cours.classe else 'Sans classe'}") for cours in get_cours_absences(ecole_id, annee, user)]


def resolve_annee_absence(absence):
    if not absence:
        return None

    # Ancrage principal : via inscription_id si présent
    if absence.inscription_id:
        insc = getattr(absence, "inscription", None)
        if insc and getattr(insc, "annee_scolaire", None):
            return insc.annee_scolaire
        insc = Inscription.query.filter_by(id=absence.inscription_id, ecole_id=absence.ecole_id).first()
        if insc and insc.annee_scolaire:
            return insc.annee_scolaire

    # Fallback historique pour absences SANS inscription_id (inscription_id IS NULL)
    # Cas A : via cours (Cours -> Classe -> AnneeScolaire)
    cours = getattr(absence, "cours", None)
    if cours and getattr(cours, "classe", None):
        classe = cours.classe
        annee = getattr(classe, "annee_scolaire", None)
        if (
            annee
            and cours.ecole_id == absence.ecole_id
            and classe.ecole_id == absence.ecole_id
            and annee.ecole_id == absence.ecole_id
        ):
            return annee

    # Cas B : sans cours, rattachement par date UNIQUEMENT si la correspondance est unique
    if absence.ecole_id and absence.date_absence:
        annees_candidates = (
            AnneeScolaire.query.filter(
                AnneeScolaire.ecole_id == absence.ecole_id,
                AnneeScolaire.date_debut <= absence.date_absence,
                AnneeScolaire.date_fin >= absence.date_absence,
            ).all()
        )
        # RÈGLE OBLIGATOIRE :
        # - Si 0 correspondance -> non rattachée
        # - Si >1 correspondances (chevauchement) -> ambiguë, NE PAS l'attribuer automatiquement
        # - Ne jamais départager via Eleve.classe_id
        if len(annees_candidates) == 1:
            annee = annees_candidates[0]
            if get_inscription_eleve_annee(absence.ecole_id, absence.eleve_id, annee.id):
                return annee

    return None


def _absence_autorisee_pour_user(absence, inscription, user):
    role = getattr(user, "role", None)
    if role == "parent":
        return absence.eleve_id in _parent_enfant_ids(user)
    if role == "professeur":
        professeur = _professeur(user)
        if not professeur:
            return False
        if absence.cours_id:
            return bool(absence.cours and absence.cours.professeur_id == professeur.id)
        return bool(inscription and inscription.classe_id in _professeur_classe_ids(user, inscription.annee_scolaire_id))
    return _is_admin_like(user)


def attacher_contexte_annuel(absence, inscription, annee):
    absence.annee_inscription = inscription
    absence.annee_classe = inscription.classe if inscription else None
    absence.annee_scolaire = annee
    return absence


def get_absences_annee(ecole_id, annee, user):
    if not ecole_id or not annee:
        return []

    inscriptions = {
        ins.eleve_id: ins
        for ins in get_inscriptions_absences(ecole_id, annee, user)
    }
    if not inscriptions:
        return []

    inscription_ids = {ins.id for ins in inscriptions.values()}

    query = (
        Absence.query.options(
            joinedload(Absence.eleve),
            joinedload(Absence.cours).joinedload(Cours.classe),
        )
        .filter(
            Absence.ecole_id == ecole_id,
            Absence.eleve_id.in_(inscriptions.keys()),
        )
    )

    absences = []
    for absence in query.all():
        # Chemin rapide : inscription_id est défini et appartient à l'année consultée
        if absence.inscription_id:
            if absence.inscription_id in inscription_ids:
                inscription = inscriptions.get(absence.eleve_id)
                if not _absence_autorisee_pour_user(absence, inscription, user):
                    continue
                absences.append(attacher_contexte_annuel(absence, inscription, annee))
            # Si inscription_id est défini mais appartient à une autre année : JAMAIS afficher ici
            continue

        # Chemin de secours : uniquement pour absences historiques sans inscription_id (NULL)
        annee_absence = resolve_annee_absence(absence)
        if not annee_absence or annee_absence.id != annee.id:
            continue
        inscription = inscriptions.get(absence.eleve_id)
        if not _absence_autorisee_pour_user(absence, inscription, user):
            continue
        absences.append(attacher_contexte_annuel(absence, inscription, annee))

    absences.sort(
        key=lambda absence: (
            absence.date_absence,
            absence.annee_classe.nom if absence.annee_classe else "",
            absence.eleve.nom if absence.eleve else "",
        ),
        reverse=True,
    )
    return absences


def verifier_mutation_absence(ecole_id, annee, user, eleve_id, cours_id, date_absence, absence=None):
    if not absences_modifiables(annee, user):
        return None, None, None, statut_annee_absences(annee)

    if not date_absence or date_absence < annee.date_debut or date_absence > annee.date_fin:
        return None, None, None, "La date d'absence doit appartenir à l'année scolaire consultée."

    inscription = get_inscription_eleve_annee(ecole_id, eleve_id, annee.id)
    if not inscription or not inscription.eleve or inscription.eleve.ecole_id != ecole_id:
        return None, None, None, "Élève non inscrit dans l'année scolaire consultée."

    cours = None
    if cours_id:
        cours = Cours.query.options(selectinload(Cours.classe)).filter_by(id=cours_id, ecole_id=ecole_id).first()
        if not cours or not cours.classe or cours.classe.annee_scolaire_id != annee.id:
            return None, None, None, "Cours non disponible pour l'année scolaire consultée."
        if cours.classe_id != inscription.classe_id:
            return None, None, None, "Le cours choisi ne correspond pas à la classe annuelle de l'élève."

    role = getattr(user, "role", None)
    if role == "professeur":
        professeur = _professeur(user)
        if not professeur:
            return None, None, None, "Profil professeur introuvable."
        if cours:
            if cours.professeur_id != professeur.id:
                return None, None, None, "Cours non autorisé pour ce professeur."
        elif inscription.classe_id not in _professeur_classe_ids(user, annee.id):
            return None, None, None, "Élève non autorisé pour ce professeur."
    elif role != "admin":
        return None, None, None, "Rôle non autorisé."

    if absence:
        annee_absence = resolve_annee_absence(absence)
        if not annee_absence or annee_absence.id != annee.id:
            return None, None, None, "Cette absence n'appartient pas à l'année scolaire consultée."

    return inscription.eleve, cours, inscription, None


def get_palmares_absences_annuel(ecole_id, annee):
    """Calcule le palmarès d'assiduité annuel :
    - Top 5 classes les plus touchées (alertes absentéisme)
    - Top 5 classes les plus assidues (reconnaissance / assiduité exemplaire)
    - Classe spotlight la plus touchée et classe spotlight la plus assidue
    - Indicateurs globaux (total absences, NJ, J, taux justification)
    - Données pour le graphique comparatif Chart.js
    """
    empty_result = {
        "classes_plus_touchees": [],
        "classes_plus_assidues": [],
        "classe_plus_absente": None,
        "classe_plus_assidue": None,
        "statistiques": {
            "total_absences": 0,
            "absences_justifiees": 0,
            "absences_non_justifiees": 0,
            "taux_justification": 100.0,
            "total_classes": 0,
            "total_eleves": 0,
        },
        "chart_data": {
            "labels": [],
            "absences": [],
            "justifiees": [],
            "non_justifiees": [],
            "is_max": [],
        },
    }

    if not ecole_id or not annee:
        return empty_result

    # 1. Classes de l'année ordonnées pédagogiquement
    classes_query = (
        Classe.query.options(joinedload(Classe.niveau_scolaire))
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
    )
    classes = classes_triees_pedagogique(classes_query).all()
    if not classes:
        return empty_result

    # 2. Effectifs par classe
    effectifs_raw = (
        db.session.query(Inscription.classe_id, func.count(Inscription.id))
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
            Inscription.classe_id.isnot(None),
        )
        .group_by(Inscription.classe_id)
        .all()
    )
    effectifs_map = {row[0]: int(row[1]) for row in effectifs_raw}

    # 3. Statistiques d'absences par classe (via Inscription)
    absences_raw = (
        db.session.query(
            Inscription.classe_id,
            func.count(Absence.id).label("total"),
            func.sum(case((Absence.justifiee == True, 1), else_=0)).label("justifiees"),
            func.sum(case((or_(Absence.justifiee == False, Absence.justifiee.is_(None)), 1), else_=0)).label("non_justifiees"),
        )
        .join(Absence, Absence.inscription_id == Inscription.id)
        .filter(
            Absence.ecole_id == ecole_id,
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
        )
        .group_by(Inscription.classe_id)
        .all()
    )
    abs_stats_map = {
        row[0]: {
            "total": int(row[1] or 0),
            "justifiees": int(row[2] or 0),
            "non_justifiees": int(row[3] or 0),
        }
        for row in absences_raw
    }

    classes_data = []
    total_absences_ecole = 0
    total_absences_justifiees = 0
    total_absences_non_justifiees = 0
    total_eleves = sum(effectifs_map.values())

    for cl in classes:
        effectif = effectifs_map.get(cl.id, 0)
        c_abs = abs_stats_map.get(cl.id, {"total": 0, "justifiees": 0, "non_justifiees": 0})
        tot = c_abs["total"]
        just = c_abs["justifiees"]
        non_just = c_abs["non_justifiees"]

        total_absences_ecole += tot
        total_absences_justifiees += just
        total_absences_non_justifiees += non_just

        taux_absenteisme = round(tot / effectif, 1) if effectif > 0 else 0.0

        classes_data.append({
            "id": cl.id,
            "nom": cl.nom,
            "niveau": getattr(cl, "niveau", "") or (cl.niveau_scolaire.nom if getattr(cl, "niveau_scolaire", None) else ""),
            "effectif": effectif,
            "total_absences": tot,
            "justifiees": just,
            "non_justifiees": non_just,
            "taux_absenteisme": taux_absenteisme,
            "is_max": False,
        })

    # Classe la plus touchée
    classes_avec_absences = [c for c in classes_data if c["total_absences"] > 0]
    classe_plus_absente = None
    if classes_avec_absences:
        classe_plus_absente = max(
            classes_avec_absences,
            key=lambda c: (c["total_absences"], c["taux_absenteisme"])
        )
        for c in classes_data:
            if c["id"] == classe_plus_absente["id"]:
                c["is_max"] = True
                break

    # Classe la plus assidue
    classes_avec_eleves = [c for c in classes_data if c["effectif"] > 0]
    classe_plus_assidue = None
    if classes_avec_eleves:
        classe_plus_assidue = min(
            classes_avec_eleves,
            key=lambda c: (c["total_absences"], c["taux_absenteisme"])
        )

    # Top 5 des classes les plus touchées (rouge)
    classes_plus_touchees = sorted(
        classes_avec_absences,
        key=lambda c: (c["total_absences"], c["taux_absenteisme"]),
        reverse=True
    )[:5]

    # Top 5 des classes les plus assidues (vert)
    classes_plus_assidues = sorted(
        classes_avec_eleves,
        key=lambda c: (c["total_absences"], c["taux_absenteisme"]),
        reverse=False
    )[:5]

    statistiques = {
        "total_absences": total_absences_ecole,
        "absences_justifiees": total_absences_justifiees,
        "absences_non_justifiees": total_absences_non_justifiees,
        "taux_justification": round((total_absences_justifiees / total_absences_ecole) * 100, 1) if total_absences_ecole > 0 else 100.0,
        "total_classes": len(classes),
        "total_eleves": total_eleves,
    }

    chart_data = {
        "labels": [c["nom"] for c in classes_data],
        "absences": [c["total_absences"] for c in classes_data],
        "justifiees": [c["justifiees"] for c in classes_data],
        "non_justifiees": [c["non_justifiees"] for c in classes_data],
        "is_max": [c["is_max"] for c in classes_data],
    }

    return {
        "classes_plus_touchees": classes_plus_touchees,
        "classes_plus_assidues": classes_plus_assidues,
        "classe_plus_absente": classe_plus_absente,
        "classe_plus_assidue": classe_plus_assidue,
        "statistiques": statistiques,
        "chart_data": chart_data,
    }

