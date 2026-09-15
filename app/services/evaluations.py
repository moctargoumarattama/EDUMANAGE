"""
app/services/evaluations.py
===========================
Service centralisé et canonique pour la complétude des évaluations,
les moyennes provisoires vs officielles, le classement et les taux de réussite dans KLASORA.
"""
from collections import defaultdict
from sqlalchemy.orm import joinedload
from app import db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
)
from app.services.notes_annuelles import (
    TYPES_CONTROLE_CONTINU,
    TYPE_COMPOSITION,
    calculer_moyenne_controles,
    calculer_moyenne_matiere_semestre,
    calculer_points_matiere,
)

STATUS_NON_EVALUE = "non_evalue"
STATUS_PROVISOIRE = "provisoire"
STATUS_COMPLETE = "complete"

LABEL_NON_EVALUE = "Non évalué"
LABEL_PROVISOIRE = "Moyenne provisoire"
LABEL_COMPLETE = "Moyenne générale"


def get_cours_attendus_classe(ecole_id, classe_id, annee_id):
    """
    Retourne la liste des cours actifs pour une classe et une année donnée.
    """
    if not ecole_id or not classe_id or not annee_id:
        return []
    return (
        Cours.query.filter(
            Cours.ecole_id == ecole_id,
            Cours.classe_id == classe_id,
        )
        .order_by(Cours.nom.asc())
        .all()
    )


def calculer_completude_inscription(ecole_id, annee_id, inscription, periode=None):
    """
    Source de vérité pour l'état d'évaluation d'une inscription sur une période donnée.
    
    Retourne un dictionnaire structuré :
    {
        "status": "non_evalue" | "provisoire" | "complete",
        "average": float | None,
        "evaluated_subjects": int,
        "expected_subjects": int,
        "completion_ratio": float,        # 0.0 à 1.0
        "completion_percent": float,      # 0.0 à 100.0 (pondéré par coefs si dispo)
        "is_official": bool,              # True ssi status == "complete"
        "evaluated_coefficients": float,
        "expected_coefficients": float,
        "label": str,
        "disciplines_details": dict
    }
    """
    if not inscription or not ecole_id or not annee_id:
        return {
            "status": STATUS_NON_EVALUE,
            "average": None,
            "evaluated_subjects": 0,
            "expected_subjects": 0,
            "completion_ratio": 0.0,
            "completion_percent": 0.0,
            "is_official": False,
            "evaluated_coefficients": 0.0,
            "expected_coefficients": 0.0,
            "label": LABEL_NON_EVALUE,
            "disciplines_details": {},
        }

    classe_id = inscription.classe_id
    cours_attendus = get_cours_attendus_classe(ecole_id, classe_id, annee_id)
    expected_subjects = len(cours_attendus)
    expected_coefficients = sum(c.coefficient if (c.coefficient and c.coefficient > 0) else 1.0 for c in cours_attendus)

    # Récupération des notes de l'inscription pour la période
    query = Note.query.filter(
        Note.inscription_id == inscription.id,
        Note.ecole_id == ecole_id,
        Note.annee_id == annee_id,
    )
    if periode:
        query = query.filter(Note.periode == periode)
    notes = query.all()

    # Regroupement par cours_id
    notes_par_cours = defaultdict(list)
    for n in notes:
        notes_par_cours[n.cours_id].append(n)

    evaluated_subjects = 0
    evaluated_coefficients = 0.0
    matieres_finalisees = []
    disciplines_details = {}

    # Map cours attendus
    cours_map = {c.id: c for c in cours_attendus}
    
    # Traitement de chaque cours attendu
    for c_id, c_obj in cours_map.items():
        c_notes = notes_par_cours.get(c_id, [])
        coef = c_obj.coefficient if (c_obj.coefficient and c_obj.coefficient > 0) else 1.0

        if c_notes:
            controles = [n for n in c_notes if n.type_evaluation in TYPES_CONTROLE_CONTINU]
            comp = next((n for n in c_notes if n.type_evaluation == TYPE_COMPOSITION), None)

            moy_ctrl = calculer_moyenne_controles(controles)
            n_comp = comp.valeur if comp else None
            moy_sem = calculer_moyenne_matiere_semestre(moy_ctrl, n_comp)

            # Fallback moyenne simple si modèle composition incomplet
            if moy_sem is None and c_notes:
                tot_p = sum((n.valeur or 0.0) * (n.coefficient or 1.0) for n in c_notes)
                tot_c = sum((n.coefficient or 1.0) for n in c_notes)
                moy_sem = round(tot_p / tot_c, 2) if tot_c > 0 else None

            if moy_sem is not None:
                evaluated_subjects += 1
                evaluated_coefficients += coef
                pts = round(moy_sem * coef, 2)
                matieres_finalisees.append({"moyenne": moy_sem, "coefficient": coef, "points": pts})
                disciplines_details[c_id] = {
                    "cours": c_obj,
                    "moyenne": moy_sem,
                    "coefficient": coef,
                    "points": pts,
                    "evalue": True
                }
            else:
                disciplines_details[c_id] = {
                    "cours": c_obj,
                    "moyenne": None,
                    "coefficient": coef,
                    "points": None,
                    "evalue": False
                }
        else:
            disciplines_details[c_id] = {
                "cours": c_obj,
                "moyenne": None,
                "coefficient": coef,
                "points": None,
                "evalue": False
            }

    # Prise en compte de cours optionnels ou hors liste avec des notes
    for c_id, c_notes in notes_par_cours.items():
        if c_id not in cours_map:
            c_obj = c_notes[0].cours if (c_notes and c_notes[0].cours) else None
            coef = c_obj.coefficient if (c_obj and c_obj.coefficient) else 1.0
            tot_p = sum((n.valeur or 0.0) * (n.coefficient or 1.0) for n in c_notes)
            tot_c = sum((n.coefficient or 1.0) for n in c_notes)
            moy_sem = round(tot_p / tot_c, 2) if tot_c > 0 else None
            if moy_sem is not None:
                evaluated_subjects += 1
                evaluated_coefficients += coef
                pts = round(moy_sem * coef, 2)
                matieres_finalisees.append({"moyenne": moy_sem, "coefficient": coef, "points": pts})

    # RÈGLE MANDATAIRE : Si expected_subjects == 0, aucune matière/cours attendu défini.
    # Ne JAMAIS produire status = complete par simple division ou liste vide.
    if expected_subjects == 0:
        return {
            "status": STATUS_NON_EVALUE,
            "average": None,
            "evaluated_subjects": evaluated_subjects,
            "expected_subjects": 0,
            "completion_ratio": 0.0,
            "completion_percent": 0.0,
            "is_official": False,
            "evaluated_coefficients": 0.0,
            "expected_coefficients": 0.0,
            "label": LABEL_NON_EVALUE,
            "disciplines_details": {},
        }

    # Calcul des ratios et moyennes
    if evaluated_coefficients > expected_coefficients:
        evaluated_coefficients = expected_coefficients

    completion_ratio = round(evaluated_coefficients / expected_coefficients, 4) if expected_coefficients > 0 else 0.0
    completion_percent = round(completion_ratio * 100.0, 1)

    if evaluated_subjects == 0:
        status = STATUS_NON_EVALUE
        average = None
        is_official = False
        label = LABEL_NON_EVALUE
    elif evaluated_subjects < expected_subjects:
        status = STATUS_PROVISOIRE
        tot_pts = sum(m["points"] for m in matieres_finalisees)
        tot_coefs = sum(m["coefficient"] for m in matieres_finalisees)
        average = round(tot_pts / tot_coefs, 2) if tot_coefs > 0 else None
        is_official = False
        label = LABEL_PROVISOIRE
    else:
        status = STATUS_COMPLETE
        tot_pts = sum(m["points"] for m in matieres_finalisees)
        tot_coefs = sum(m["coefficient"] for m in matieres_finalisees)
        average = round(tot_pts / tot_coefs, 2) if tot_coefs > 0 else None
        is_official = True
        label = LABEL_COMPLETE

    return {
        "status": status,
        "average": average,
        "evaluated_subjects": evaluated_subjects,
        "expected_subjects": expected_subjects,
        "completion_ratio": completion_ratio,
        "completion_percent": completion_percent,
        "is_official": is_official,
        "evaluated_coefficients": evaluated_coefficients,
        "expected_coefficients": expected_coefficients,
        "label": label,
        "disciplines_details": disciplines_details,
    }


def calculer_stats_et_classements_classe(ecole_id, classe_id, annee_id, periode=None):
    """
    Calcule les classements et statistiques de classe en filtrant STRICTEMENT les évaluations complètes.
    
    Retourne :
    {
        "rangs_par_inscription": { inscription_id: rang },
        "effectif_total": int,
        "complets_count": int,
        "provisoires_count": int,
        "non_evalues_count": int,
        "taux_reussite": float | None,
        "moyenne_classe_officielle": float | None,
        "meilleur_eleve_complet": dict | None,
        "plus_forte_moyenne": float | None,
        "plus_faible_moyenne": float | None,
    }
    """
    if not ecole_id or not classe_id or not annee_id:
        return {
            "rangs_par_inscription": {},
            "effectif_total": 0,
            "complets_count": 0,
            "provisoires_count": 0,
            "non_evalues_count": 0,
            "taux_reussite": None,
            "moyenne_classe_officielle": None,
            "meilleur_eleve_complet": None,
            "plus_forte_moyenne": None,
            "plus_faible_moyenne": None,
        }

    inscriptions = (
        Inscription.query.options(joinedload(Inscription.eleve))
        .filter_by(ecole_id=ecole_id, classe_id=classe_id, annee_scolaire_id=annee_id)
        .all()
    )

    effectif_total = len(inscriptions)
    if effectif_total == 0:
        return {
            "rangs_par_inscription": {},
            "effectif_total": 0,
            "complets_count": 0,
            "provisoires_count": 0,
            "non_evalues_count": 0,
            "taux_reussite": None,
            "moyenne_classe_officielle": None,
            "meilleur_eleve_complet": None,
            "plus_forte_moyenne": None,
            "plus_faible_moyenne": None,
        }

    evals_by_ins = {}
    complets = []
    provisoires = []
    non_evalues = []

    for ins in inscriptions:
        ev = calculer_completude_inscription(ecole_id, annee_id, ins, periode=periode)
        evals_by_ins[ins.id] = ev
        if ev["status"] == STATUS_COMPLETE:
            complets.append((ins, ev))
        elif ev["status"] == STATUS_PROVISOIRE:
            provisoires.append((ins, ev))
        else:
            non_evalues.append((ins, ev))

    # Classement : UNIQUEMENT les élèves complets
    complets.sort(key=lambda item: item[1]["average"] if item[1]["average"] is not None else -1.0, reverse=True)

    rangs_par_inscription = {}
    for rank, (ins, ev) in enumerate(complets, 1):
        rangs_par_inscription[ins.id] = rank

    complets_count = len(complets)
    if complets_count > 0:
        # FORMULE MANDATAIRE DU TAUX DE RÉUSSITE :
        # (nombre d'élèves status == complete et moyenne >= 10) / total complets * 100
        admis = sum(1 for ins, ev in complets if ev["average"] is not None and ev["average"] >= 10.0)
        taux_reussite = round((admis / complets_count) * 100.0, 1)
        moyennes_complets = [ev["average"] for ins, ev in complets if ev["average"] is not None]
        moyenne_classe_officielle = round(sum(moyennes_complets) / len(moyennes_complets), 2) if moyennes_complets else None
        plus_forte_moyenne = max(moyennes_complets) if moyennes_complets else None
        plus_faible_moyenne = min(moyennes_complets) if moyennes_complets else None
        top_ins, top_ev = complets[0]
        meilleur_eleve_complet = {
            "inscription": top_ins,
            "eleve": top_ins.eleve,
            "average": top_ev["average"]
        }
    else:
        taux_reussite = None
        moyenne_classe_officielle = None
        plus_forte_moyenne = None
        plus_faible_moyenne = None
        meilleur_eleve_complet = None

    return {
        "rangs_par_inscription": rangs_par_inscription,
        "effectif_total": effectif_total,
        "complets_count": complets_count,
        "provisoires_count": len(provisoires),
        "non_evalues_count": len(non_evalues),
        "taux_reussite": taux_reussite,
        "moyenne_classe_officielle": moyenne_classe_officielle,
        "meilleur_eleve_complet": meilleur_eleve_complet,
        "plus_forte_moyenne": plus_forte_moyenne,
        "plus_faible_moyenne": plus_faible_moyenne,
    }

