from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import joinedload

from app.models import (
    Absence,
    Bulletin,
    Classe,
    Cours,
    Eleve,
    EmploiTemps,
    Inscription,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
    db,
)
from app.services.bulletins_annuels import calculer_bulletin_data
from app.services.notes_annuelles import SEMESTRE_1, SEMESTRE_2
from app.services.paiements_annuels import get_finances_inscription
from app.utils_classes import classes_triees_pedagogique


def _empty_admin_stats():
    return {
        "total_eleves": 0,
        "total_professeurs": 0,
        "total_cours": 0,
        "total_cours_affectes": 0,
        "paiements_attente": 0,
        "eleves_nouveaux": 0,
    }


def _inscription_ids(ecole_id, annee_id):
    if not ecole_id or not annee_id:
        return []
    return [
        row[0]
        for row in db.session.query(Inscription.id)
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_id,
        )
        .all()
    ]


def _classes_annee(ecole_id, annee_id):
    if not ecole_id or not annee_id:
        return []
    from flask import g
    key = (ecole_id, annee_id)
    try:
        if not hasattr(g, '_classes_annee_cache'):
            g._classes_annee_cache = {}
        if key in g._classes_annee_cache:
            return g._classes_annee_cache[key]
    except RuntimeError:
        pass

    classes = classes_triees_pedagogique(
        Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_id)
    ).all()
    try:
        if hasattr(g, '_classes_annee_cache'):
            g._classes_annee_cache[key] = classes
    except RuntimeError:
        pass
    return classes


def get_dashboard_admin_annuel(ecole_id, annee):
    stats = _empty_admin_stats()
    if not ecole_id or not annee:
        return stats

    annee_id = annee.id
    classes = _classes_annee(ecole_id, annee_id)
    classe_ids = [c.id for c in classes]
    debut_mois = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    from sqlalchemy.orm import selectinload
    inscriptions = (
        Inscription.query
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_id
        )
        .options(selectinload(Inscription.paiements))
        .all()
    )

    stats["total_eleves"] = len(inscriptions)
    stats["total_professeurs"] = Professeur.query.filter_by(ecole_id=ecole_id).count()
    stats["total_cours"] = (
        Cours.query.filter(Cours.ecole_id == ecole_id, Cours.classe_id.in_(classe_ids)).count()
        if classe_ids
        else 0
    )
    stats["total_cours_affectes"] = (
        Cours.query.filter(
            Cours.ecole_id == ecole_id,
            Cours.classe_id.in_(classe_ids),
            Cours.professeur_id.isnot(None),
        ).count()
        if classe_ids
        else 0
    )

    nb_impayes = 0
    eleves_nouveaux = 0
    for ins in inscriptions:
        fin = get_finances_inscription(ins)
        if fin.get("reste_a_payer", 0) > 0:
            nb_impayes += 1
        if ins.date_inscription and ins.date_inscription >= debut_mois:
            eleves_nouveaux += 1

    stats["paiements_attente"] = nb_impayes
    stats["eleves_nouveaux"] = eleves_nouveaux
    return stats


def get_professeur_dashboard_annuel(ecole_id, annee, professeur_id):
    if not ecole_id or not annee or not professeur_id:
        return {"mes_cours": [], "stats": {"total_eleves": 0, "total_cours": 0, "moyenne_generale": 0}, "dernieres_notes": []}

    classe_ids = [c.id for c in _classes_annee(ecole_id, annee.id)]
    mes_cours = (
        Cours.query.filter(
            Cours.ecole_id == ecole_id,
            Cours.professeur_id == professeur_id,
            Cours.classe_id.in_(classe_ids),
        )
        .options(joinedload(Cours.classe))
        .order_by(Cours.nom.asc())
        .all()
        if classe_ids
        else []
    )
    cours_ids = [c.id for c in mes_cours]
    inscription_ids = _inscription_ids(ecole_id, annee.id)

    if cours_ids and inscription_ids:
        notes_query = Note.query.filter(
            Note.ecole_id == ecole_id,
            Note.cours_id.in_(cours_ids),
            Note.inscription_id.in_(inscription_ids),
        )
        stats_row = (
            db.session.query(
                db.func.count(db.func.distinct(Note.eleve_id)),
                db.func.avg(Note.valeur)
            )
            .filter(
                Note.ecole_id == ecole_id,
                Note.cours_id.in_(cours_ids),
                Note.inscription_id.in_(inscription_ids),
            )
            .first()
        )
        total_eleves = stats_row[0] if stats_row else 0
        moyenne = round(float(stats_row[1]), 2) if (stats_row and stats_row[1] is not None) else 0

        dernieres_notes = (
            notes_query
            .options(
                joinedload(Note.eleve),
                joinedload(Note.cours).joinedload(Cours.classe),
                joinedload(Note.inscription).joinedload(Inscription.classe)
            )
            .order_by(Note.date_evaluation.desc(), Note.id.desc())
            .limit(5)
            .all()
        )
    else:
        total_eleves = 0
        moyenne = 0
        dernieres_notes = []

    return {
        "mes_cours": mes_cours,
        "stats": {
            "total_eleves": total_eleves,
            "total_cours": len(mes_cours),
            "moyenne_generale": moyenne,
        },
        "dernieres_notes": dernieres_notes,
    }


def get_parent_enfants_query(ecole_id, annee, parent_id):
    return (
        Inscription.query.join(Eleve, Eleve.id == Inscription.eleve_id)
        .options(joinedload(Inscription.eleve), joinedload(Inscription.classe))
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
            Eleve.ecole_id == ecole_id,
            Eleve.parent_id == parent_id,
        )
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
    )


def enrichir_enfants_parent_annuel(inscriptions):
    enfants = []
    for inscription in inscriptions:
        enfant = inscription.eleve
        if not enfant:
            continue
        notes = getattr(inscription, "notes", []) or []
        from app.services.evaluations import calculer_completude_inscription
        eval_info = calculer_completude_inscription(inscription.ecole_id, inscription.annee_scolaire_id, inscription)
        enfant.classe = inscription.classe
        
        enfant.total_notes = len(notes)
        notes_sorted = sorted(notes, key=lambda n: n.date_evaluation or datetime.min, reverse=True)
        enfant.dernieres_notes = notes_sorted[:3]

        absences = getattr(inscription, "absences", []) or []
        enfant.total_absences = len(absences)
        absences_sorted = sorted(absences, key=lambda a: a.date_absence or datetime.min, reverse=True)
        enfant.dernieres_absences = absences_sorted[:2]
        
        enfant.total_paiements = len(getattr(inscription, "paiements", []) or [])
        enfants.append(enfant)
    return enfants


def get_notes_moyennes_annuelles(ecole_id, annee, professeur_id=None):
    if not ecole_id or not annee:
        return {"matieres": [], "moyennes": []}

    query = (
        db.session.query(Cours.nom, db.func.avg(Note.valeur).label("moyenne"))
        .join(Note, Note.cours_id == Cours.id)
        .join(Inscription, Inscription.id == Note.inscription_id)
        .filter(
            Cours.ecole_id == ecole_id,
            Note.ecole_id == ecole_id,
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
        )
    )
    if professeur_id:
        query = query.filter(Cours.professeur_id == professeur_id)
    result = query.group_by(Cours.nom).order_by(Cours.nom.asc()).all()
    return {
        "matieres": [row[0] for row in result],
        "moyennes": [float(row[1]) if row[1] else 0 for row in result],
    }


def get_absences_par_mois_annuelles(ecole_id, annee):
    if not ecole_id or not annee:
        return {"mois": [], "absences": []}

    query = (
        Absence.query.join(Inscription, Inscription.id == Absence.inscription_id)
        .filter(
            Absence.ecole_id == ecole_id,
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
        )
    )
    try:
        result = (
            db.session.query(
                db.func.strftime("%Y", Absence.date_absence).label("annee"),
                db.func.strftime("%m", Absence.date_absence).label("mois"),
                db.func.count(Absence.id).label("total"),
            )
            .filter(Absence.id.in_(query.with_entities(Absence.id)))
            .group_by("annee", "mois")
            .order_by("annee", "mois")
            .all()
        )
    except Exception:
        result = (
            db.session.query(
                db.func.extract("year", Absence.date_absence).label("annee"),
                db.func.extract("month", Absence.date_absence).label("mois"),
                db.func.count(Absence.id).label("total"),
            )
            .filter(Absence.id.in_(query.with_entities(Absence.id)))
            .group_by("annee", "mois")
            .order_by("annee", "mois")
            .all()
        )

    return {
        "mois": [f"{int(row.mois)}/{int(row.annee)}" for row in result],
        "absences": [int(row.total) for row in result],
    }


def get_rapport_notes_par_classe_annuel(ecole_id, annee):
    rapports = get_rapports_annuels(ecole_id, annee)
    return {
        classe_data["nom"]: classe_data["moyenne"] if classe_data["moyenne"] is not None else 0
        for classe_data in rapports["classes_data"]
    }


def get_rapport_absences_par_classe_annuel(ecole_id, annee):
    rapports = get_rapports_annuels(ecole_id, annee)
    return {
        classe_data["nom"]: classe_data["total_absences"]
        for classe_data in rapports["classes_data"]
    }


def get_rapports_annuels(ecole_id, annee):
    classes = _classes_annee(ecole_id, annee.id) if annee else []
    if not ecole_id or not annee:
        return _empty_rapports(classes)

    inscriptions = (
        Inscription.query.options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
            joinedload(Inscription.notes),
            joinedload(Inscription.absences),
        )
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
        .all()
    )
    inscriptions_par_classe = defaultdict(list)
    for inscription in inscriptions:
        inscriptions_par_classe[inscription.classe_id].append(inscription)

    classes_data = []
    total_absences_ecole = 0
    total_absences_justifiees_ecole = 0
    total_absences_non_justifiees_ecole = 0
    total_eleves_ecole = 0
    moyennes_ecole = []
    finances_ecole = {
        "frais_attendus": 0.0,
        "total_encaisse": 0.0,
        "reste_a_payer": 0.0,
        "eleves_non_payes": 0,
        "paiements_partiels": 0,
        "eleves_soldes": 0,
    }

    for classe in classes:
        classe_inscriptions = inscriptions_par_classe.get(classe.id, [])
        effectif = len(classe_inscriptions)
        total_eleves_ecole += effectif
        eleves = [i.eleve for i in classe_inscriptions if i.eleve]
        garcons = sum(1 for e in eleves if (e.genre or "").upper() != "F")
        filles = sum(1 for e in eleves if (e.genre or "").upper() == "F")

        total_absences_classe = 0
        justifiees_classe = 0
        non_justifiees_classe = 0
        eleves_stats = []
        moyennes_classe = []
        finances_classe = {
            "frais_attendus": 0.0,
            "total_encaisse": 0.0,
            "reste_a_payer": 0.0,
            "eleves_non_payes": 0,
            "paiements_partiels": 0,
            "eleves_soldes": 0,
        }

        for inscription in classe_inscriptions:
            eleve = inscription.eleve
            if not eleve:
                continue
            absences = getattr(inscription, "absences", []) or []
            nb_abs = len(absences)
            nb_just = sum(1 for a in absences if a.justifiee)
            nb_non_just = nb_abs - nb_just
            total_absences_classe += nb_abs
            justifiees_classe += nb_just
            non_justifiees_classe += nb_non_just

            from app.services.evaluations import calculer_completude_inscription, STATUS_COMPLETE
            eval_info = calculer_completude_inscription(ecole_id, annee.id, inscription)
            moyenne_eleve = eval_info["average"] if eval_info["status"] == STATUS_COMPLETE else None
            if moyenne_eleve is not None:
                moyennes_classe.append(moyenne_eleve)
                moyennes_ecole.append(moyenne_eleve)

            finances = get_finances_inscription(inscription)
            _ajouter_finances(finances_classe, finances)
            _ajouter_finances(finances_ecole, finances)

            eleves_stats.append({
                "eleve": eleve,
                "id": eleve.id,
                "nom": eleve.nom,
                "prenom": eleve.prenom,
                "genre": eleve.genre or "M",
                "nb_absences": nb_abs,
                "nb_justifiees": nb_just,
                "nb_non_justifiees": nb_non_just,
                "moyenne": moyenne_eleve,
                "eval_info": eval_info,
            })

        total_absences_ecole += total_absences_classe
        total_absences_justifiees_ecole += justifiees_classe
        total_absences_non_justifiees_ecole += non_justifiees_classe

        from app.services.evaluations import calculer_stats_et_classements_classe
        stats_c = calculer_stats_et_classements_classe(ecole_id, classe.id, annee.id)
        moyenne_classe = stats_c["moyenne_classe_officielle"]
        taux_absenteisme = round(total_absences_classe / effectif, 1) if effectif > 0 else 0.0
        top_absents = sorted(
            [s for s in eleves_stats if s["nb_absences"] > 0],
            key=lambda x: x["nb_absences"],
            reverse=True,
        )[:5]

        classes_data.append({
            "id": classe.id,
            "classe": classe,
            "nom": classe.nom,
            "niveau": getattr(classe, "niveau", "") or "",
            "salle": getattr(classe, "salle", "") or "",
            "effectif": effectif,
            "capacite": getattr(classe, "capacite", None) or getattr(classe, "capacite_max", None) or 35,
            "garcons": garcons,
            "filles": filles,
            "total_absences": total_absences_classe,
            "justifiees": justifiees_classe,
            "non_justifiees": non_justifiees_classe,
            "taux_absenteisme": taux_absenteisme,
            "moyenne": moyenne_classe,
            "finances": finances_classe,
            "top_absents": top_absents,
            "meilleur_eleve": stats_c["meilleur_eleve_complet"],
            "taux_reussite": stats_c["taux_reussite"],
            "is_most_absent": False,
        })

    classe_plus_absente = _mark_classe_plus_absente(classes_data)
    classe_plus_assidue = _classe_plus_assidue(classes_data)
    classe_meilleure_moyenne = _classe_meilleure_moyenne(classes_data)
    moyenne_generale_ecole = _moyenne_liste(moyennes_ecole)
    total_professeurs = Utilisateur.query.filter_by(role="professeur", ecole_id=ecole_id).count()

    statistiques = {
        "total_eleves": total_eleves_ecole,
        "total_professeurs": total_professeurs,
        "total_classes": len(classes),
        "total_absences": total_absences_ecole,
        "absences_justifiees": total_absences_justifiees_ecole,
        "absences_non_justifiees": total_absences_non_justifiees_ecole,
        "moyenne_generale": moyenne_generale_ecole,
        "finances": finances_ecole,
        "frais_attendus": finances_ecole["frais_attendus"],
        "total_encaisse": finances_ecole["total_encaisse"],
        "reste_a_payer": finances_ecole["reste_a_payer"],
        "eleves_non_payes": finances_ecole["eleves_non_payes"],
        "paiements_partiels": finances_ecole["paiements_partiels"],
        "taux_justification": round((total_absences_justifiees_ecole / total_absences_ecole) * 100, 1)
        if total_absences_ecole > 0
        else 100.0,
    }
    chart_data = {
        "labels": [cd["nom"] for cd in classes_data],
        "absences": [cd["total_absences"] for cd in classes_data],
        "justifiees": [cd["justifiees"] for cd in classes_data],
        "non_justifiees": [cd["non_justifiees"] for cd in classes_data],
        "moyennes": [cd["moyenne"] if cd["moyenne"] is not None else 0 for cd in classes_data],
        "is_max_absence": [cd["is_most_absent"] for cd in classes_data],
    }
    return {
        "classes": classes,
        "classes_data": classes_data,
        "classe_plus_absente": classe_plus_absente,
        "classe_plus_assidue": classe_plus_assidue,
        "classe_meilleure_moyenne": classe_meilleure_moyenne,
        "statistiques": statistiques,
        "chart_data": chart_data,
    }


def _empty_rapports(classes):
    return {
        "classes": classes,
        "classes_data": [],
        "classe_plus_absente": None,
        "classe_plus_assidue": None,
        "classe_meilleure_moyenne": None,
        "statistiques": {
            "total_eleves": 0,
            "total_professeurs": 0,
            "total_classes": len(classes),
            "total_absences": 0,
            "absences_justifiees": 0,
            "absences_non_justifiees": 0,
            "moyenne_generale": None,
            "taux_justification": 100.0,
        },
        "chart_data": {"labels": [], "absences": [], "justifiees": [], "non_justifiees": [], "moyennes": [], "is_max_absence": []},
    }


def _moyenne_notes(notes):
    if not notes:
        return None
    total_pondere = sum(n.valeur * (n.coefficient or 1.0) for n in notes)
    total_coeffs = sum(n.coefficient or 1.0 for n in notes)
    return round(total_pondere / total_coeffs, 2) if total_coeffs > 0 else 0.0


def _moyenne_liste(valeurs):
    valeurs_valides = [v for v in valeurs if v is not None]
    if not valeurs_valides:
        return None
    return round(sum(valeurs_valides) / len(valeurs_valides), 2)


def _moyenne_inscription_bulletins(ecole_id, annee, inscription):
    moyennes = []
    for periode in (SEMESTRE_1, SEMESTRE_2):
        data, err = calculer_bulletin_data(ecole_id, annee, inscription, periode=periode)
        if not err and data and data.get("moyenne_generale") is not None:
            moyennes.append(data["moyenne_generale"])
    return _moyenne_liste(moyennes)


def _ajouter_finances(total, finances):
    total["frais_attendus"] += finances["frais_annuels"]
    total["total_encaisse"] += finances["total_paye"]
    total["reste_a_payer"] += finances["reste_a_payer"]
    if finances["statut_solde"] == "aucun":
        total["eleves_non_payes"] += 1
    elif finances["statut_solde"] == "partiel":
        total["paiements_partiels"] += 1
    elif finances["statut_solde"] == "complet":
        total["eleves_soldes"] += 1


def _mark_classe_plus_absente(classes_data):
    classes_avec_absences = [cd for cd in classes_data if cd["total_absences"] > 0]
    if not classes_avec_absences:
        return None
    classe_plus_absente = max(classes_avec_absences, key=lambda x: (x["total_absences"], x["taux_absenteisme"]))
    classe_plus_absente["is_most_absent"] = True
    return classe_plus_absente


def _classe_plus_assidue(classes_data):
    classes_avec_eleves = [cd for cd in classes_data if cd["effectif"] > 0]
    if classes_avec_eleves:
        return min(classes_avec_eleves, key=lambda x: (x["total_absences"], x["taux_absenteisme"]))
    if classes_data:
        return min(classes_data, key=lambda x: x["total_absences"])
    return None


def _classe_meilleure_moyenne(classes_data):
    classes_avec_moyenne = [cd for cd in classes_data if cd["moyenne"] is not None]
    if classes_avec_moyenne:
        return max(classes_avec_moyenne, key=lambda x: x["moyenne"])
    return None
