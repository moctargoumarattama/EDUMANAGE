"""
app/services/evaluations.py
===========================
Service centralisé et canonique pour la complétude des évaluations,
les moyennes provisoires vs officielles, le classement et les taux de réussite dans KLASORA.
"""
from collections import defaultdict
from datetime import datetime
from sqlalchemy.orm import joinedload
from app import db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
    PeriodeBulletin,
)
from app.utils_classes import classes_triees_pedagogique
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
    from flask import has_app_context, g
    if has_app_context():
        if not hasattr(g, '_cours_attendus_cache'):
            g._cours_attendus_cache = {}
        key = (ecole_id, classe_id, annee_id)
        if key in g._cours_attendus_cache:
            return g._cours_attendus_cache[key]
    cours = (
        Cours.query.filter(
            Cours.ecole_id == ecole_id,
            Cours.classe_id == classe_id,
        )
        .order_by(Cours.nom.asc())
        .all()
    )
    if has_app_context():
        g._cours_attendus_cache[key] = cours
    return cours


def calculer_moyenne_matiere(notes_matiere):
    """
    Calcule la moyenne d'une matière pour une période donnée (service canonique KLASORA) :
    1. Toutes les notes de contrôle continu de la période (devoir, interrogation, etc.)
       sont additionnées puis divisées par leur nombre (moyenne arithmétique simple).
       Aucun nombre minimum arbitraire de notes : 1, 2, 3, 4+ notes fonctionnent.
    2. Si une composition / examen existe pour la période :
       Moyenne semestrielle = (Moyenne contrôle continu + Note composition) / 2.0
    3. Si aucune composition n'existe :
       La moyenne du contrôle continu est utilisée comme moyenne actuelle de la matière.
    4. Si uniquement une composition existe (aucun contrôle continu) :
       La moyenne est égale à la note de composition.
    5. Si aucune note valide :
       Retourne None.
    """
    if not notes_matiere:
        return None

    valid_notes = [n for n in notes_matiere if getattr(n, "valeur", None) is not None]
    if not valid_notes:
        return None

    controles = [n for n in valid_notes if getattr(n, "type_evaluation", None) in TYPES_CONTROLE_CONTINU]
    comp = next((n for n in valid_notes if getattr(n, "type_evaluation", None) == TYPE_COMPOSITION), None)

    moy_ctrl = calculer_moyenne_controles(controles)
    n_comp = comp.valeur if comp else None

    if moy_ctrl is not None and n_comp is not None:
        return calculer_moyenne_matiere_semestre(moy_ctrl, n_comp)
    elif moy_ctrl is not None:
        return moy_ctrl
    elif n_comp is not None:
        try:
            return round(float(n_comp), 2)
        except (ValueError, TypeError):
            return None
    else:
        valeurs = []
        for n in valid_notes:
            try:
                valeurs.append(float(n.valeur))
            except (ValueError, TypeError):
                pass
        return round(sum(valeurs) / len(valeurs), 2) if valeurs else None


def calculer_completude_inscription(ecole_id, annee_id, inscription, periode=None, periode_publiee=None, notes=None, cours_attendus=None):
    """
    Source de vérité pour l'état d'évaluation d'une inscription sur une période donnée.

    Règles officielles :
    - Un bulletin est FINAL (STATUS_COMPLETE) SSI :
      1. Toutes les matières attendues de cet élève ont au moins une note valide (complétude pédagogique).
      2. La période est explicitement publiée par l'administration (periode_publiee=True).
    - Tant que la période n'est pas publiée, le bulletin reste PROVISOIRE même avec 100% des matières notées.

    Retourne un dictionnaire structuré :
    {
        "status": "non_evalue" | "provisoire" | "complete",
        "average": float | None,
        "evaluated_subjects": int,
        "expected_subjects": int,
        "completion_ratio": float,        # 0.0 à 1.0
        "completion_percent": float,      # 0.0 à 100.0 (pondéré par coefs si dispo)
        "is_official": bool,              # True ssi status == "complete"
        "is_pedagogically_complete": bool, # True ssi toutes matières attendues notées
        "periode_publiee": bool,
        "evaluated_coefficients": float,
        "expected_coefficients": float,
        "label": str,
        "disciplines_details": dict,
        "missing_subjects": list,
        "missing_subjects_names": list[str],
    }
    """
    if periode_publiee is None:
        if ecole_id and annee_id:
            from flask import has_app_context, g
            p_obj = None
            if has_app_context():
                if not hasattr(g, '_periode_bulletin_cache'):
                    g._periode_bulletin_cache = {}
                p_key = (ecole_id, annee_id, periode)
                if p_key in g._periode_bulletin_cache:
                    p_obj = g._periode_bulletin_cache[p_key]
                else:
                    if periode:
                        p_obj = PeriodeBulletin.query.filter_by(
                            ecole_id=ecole_id,
                            annee_id=annee_id,
                            nom=periode
                        ).first()
                    else:
                        p_obj = PeriodeBulletin.query.filter_by(
                            ecole_id=ecole_id,
                            annee_id=annee_id,
                            periode_active=True
                        ).first()
                        if not p_obj:
                            p_obj = PeriodeBulletin.query.filter_by(
                                ecole_id=ecole_id,
                                annee_id=annee_id,
                                publie=True
                            ).first()
                    g._periode_bulletin_cache[p_key] = p_obj
            else:
                if periode:
                    p_obj = PeriodeBulletin.query.filter_by(
                        ecole_id=ecole_id,
                        annee_id=annee_id,
                        nom=periode
                    ).first()
                else:
                    p_obj = PeriodeBulletin.query.filter_by(
                        ecole_id=ecole_id,
                        annee_id=annee_id,
                        periode_active=True
                    ).first()
                    if not p_obj:
                        p_obj = PeriodeBulletin.query.filter_by(
                            ecole_id=ecole_id,
                            annee_id=annee_id,
                            publie=True
                        ).first()
            est_publiee = bool(p_obj and p_obj.publie)
        else:
            est_publiee = False
    else:
        est_publiee = bool(periode_publiee)

    if not inscription or not ecole_id or not annee_id:
        return {
            "status": STATUS_NON_EVALUE,
            "average": None,
            "evaluated_subjects": 0,
            "expected_subjects": 0,
            "completion_ratio": 0.0,
            "completion_percent": 0.0,
            "is_official": False,
            "is_pedagogically_complete": False,
            "periode_publiee": est_publiee,
            "evaluated_coefficients": 0.0,
            "expected_coefficients": 0.0,
            "label": LABEL_NON_EVALUE,
            "disciplines_details": {},
            "missing_subjects": [],
            "missing_subjects_names": [],
        }

    classe_id = inscription.classe_id
    if cours_attendus is None:
        cours_attendus = get_cours_attendus_classe(ecole_id, classe_id, annee_id)
    expected_subjects = len(cours_attendus)
    expected_coefficients = sum(c.coefficient if (c.coefficient and c.coefficient > 0) else 1.0 for c in cours_attendus)

    # Récupération des notes de l'inscription pour la période
    if notes is None:
        query = Note.query.filter(
            Note.inscription_id == inscription.id,
            Note.ecole_id == ecole_id,
            Note.annee_id == annee_id,
        )
        if periode:
            query = query.filter(Note.periode == periode)
        notes_list = query.all()
    else:
        if periode:
            notes_list = [n for n in notes if getattr(n, "periode", None) == periode or getattr(n, "periode", None) is None]
        else:
            notes_list = list(notes)

    # Regroupement par cours_id
    notes_par_cours = defaultdict(list)
    for n in notes_list:
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
            moy_sem = calculer_moyenne_matiere(c_notes)
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
                    "evalue": True,
                    "notes_count": len(c_notes),
                }
            else:
                disciplines_details[c_id] = {
                    "cours": c_obj,
                    "moyenne": None,
                    "coefficient": coef,
                    "points": None,
                    "evalue": False,
                    "notes_count": 0,
                }
        else:
            disciplines_details[c_id] = {
                "cours": c_obj,
                "moyenne": None,
                "coefficient": coef,
                "points": None,
                "evalue": False,
                "notes_count": 0,
            }

    # Prise en compte de cours optionnels ou hors liste avec des notes
    for c_id, c_notes in notes_par_cours.items():
        if c_id not in cours_map:
            c_obj = c_notes[0].cours if (c_notes and c_notes[0].cours) else None
            coef = c_obj.coefficient if (c_obj and c_obj.coefficient) else 1.0
            moy_sem = calculer_moyenne_matiere(c_notes)
            if moy_sem is not None:
                evaluated_subjects += 1
                evaluated_coefficients += coef
                pts = round(moy_sem * coef, 2)
                matieres_finalisees.append({"moyenne": moy_sem, "coefficient": coef, "points": pts})

    missing_subjects = [
        d["cours"] for d in disciplines_details.values() if not d["evalue"]
    ]
    missing_subjects_names = [c.nom for c in missing_subjects]
    is_pedagogically_complete = (expected_subjects > 0 and evaluated_subjects >= expected_subjects)

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
            "is_pedagogically_complete": False,
            "periode_publiee": est_publiee,
            "evaluated_coefficients": 0.0,
            "expected_coefficients": 0.0,
            "label": LABEL_NON_EVALUE,
            "disciplines_details": {},
            "missing_subjects": [],
            "missing_subjects_names": [],
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
    elif not is_pedagogically_complete:
        status = STATUS_PROVISOIRE
        tot_pts = sum(m["points"] for m in matieres_finalisees)
        tot_coefs = sum(m["coefficient"] for m in matieres_finalisees)
        average = round(tot_pts / tot_coefs, 2) if tot_coefs > 0 else None
        is_official = False
        label = LABEL_PROVISOIRE
    else:
        # Pédagogiquement complet : toutes les matières attendues ont au moins une note
        tot_pts = sum(m["points"] for m in matieres_finalisees)
        tot_coefs = sum(m["coefficient"] for m in matieres_finalisees)
        average = round(tot_pts / tot_coefs, 2) if tot_coefs > 0 else None

        # Le bulletin individuel est FINAL (officiel) SSI la période est publiée
        if est_publiee:
            status = STATUS_COMPLETE
            is_official = True
            label = LABEL_COMPLETE
        else:
            status = STATUS_PROVISOIRE
            is_official = False
            label = LABEL_PROVISOIRE

    return {
        "status": status,
        "average": average,
        "evaluated_subjects": evaluated_subjects,
        "expected_subjects": expected_subjects,
        "completion_ratio": completion_ratio,
        "completion_percent": completion_percent,
        "is_official": is_official,
        "is_pedagogically_complete": is_pedagogically_complete,
        "periode_publiee": est_publiee,
        "evaluated_coefficients": evaluated_coefficients,
        "expected_coefficients": expected_coefficients,
        "label": label,
        "disciplines_details": disciplines_details,
        "missing_subjects": missing_subjects,
        "missing_subjects_names": missing_subjects_names,
    }


def preparer_dossier_notes_eleve(notes_eleve):
    """
    Organise les notes d'un élève matière par matière pour la page Notes.
    Retourne une liste ordonnée de dictionnaires :
    [
        {
            "cours": cours_obj,
            "cours_nom": str,
            "notes_count": int,
            "moyenne_matiere": float | None,
            "notes": list[Note]
        },
        ...
    ]
    """
    if not notes_eleve:
        return []

    notes_par_cours = defaultdict(list)
    for n in notes_eleve:
        c_id = n.cours_id or (n.cours.id if getattr(n, "cours", None) else None) or 0
        notes_par_cours[c_id].append(n)

    matieres = []
    for c_id, c_notes in notes_par_cours.items():
        c_obj = c_notes[0].cours if (c_notes and getattr(c_notes[0], "cours", None)) else None
        c_nom = c_obj.nom if c_obj else "Autre matière"
        moy_mat = calculer_moyenne_matiere(c_notes)
        sorted_notes = sorted(
            c_notes,
            key=lambda n: getattr(n, "date_evaluation", None) or datetime.min,
            reverse=True
        )
        matieres.append({
            "cours": c_obj,
            "cours_nom": c_nom,
            "notes_count": len(c_notes),
            "moyenne_matiere": moy_mat,
            "notes": sorted_notes,
        })

    matieres.sort(key=lambda m: (m["cours_nom"] or "").lower())
    return matieres


def calculer_stats_et_classements_classe(ecole_id, classe_id, annee_id, periode=None, periode_publiee=None, precomputed_evals=None):
    """
    Calcule les classements et statistiques de classe en filtrant STRICTEMENT les évaluations complètes.
    
    Règles officielles :
    - Tant que la période n'est pas publiée (periode_publiee=False), aucun élève n'est complet
      officiellement : les rangs sont None, moyenne_classe = None, top = None, taux_reussite = None.
    - Seuls les élèves individuellement complets au sein d'une période publiée participent au classement
      et aux statistiques de classe.

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
    if periode_publiee is None:
        if ecole_id and annee_id:
            if periode:
                p_obj = PeriodeBulletin.query.filter_by(
                    ecole_id=ecole_id,
                    annee_id=annee_id,
                    nom=periode
                ).first()
            else:
                p_obj = PeriodeBulletin.query.filter_by(
                    ecole_id=ecole_id,
                    annee_id=annee_id,
                    periode_active=True
                ).first()
                if not p_obj:
                    p_obj = PeriodeBulletin.query.filter_by(
                        ecole_id=ecole_id,
                        annee_id=annee_id,
                        publie=True
                    ).first()
            periode_publiee = bool(p_obj and p_obj.publie)
        else:
            periode_publiee = False

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

    if precomputed_evals is not None:
        effectif_total = len(precomputed_evals)
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
        for item in precomputed_evals:
            if isinstance(item, (tuple, list)):
                ins, ev = item[0], item[1]
            else:
                ins = item.get('inscription')
                ev = item.get('eval_info')
            if not ins or not ev:
                continue
            evals_by_ins[ins.id] = ev
            if ev.get("status") == STATUS_COMPLETE:
                complets.append((ins, ev))
            elif ev.get("status") == STATUS_PROVISOIRE:
                provisoires.append((ins, ev))
            else:
                non_evalues.append((ins, ev))
    else:
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
            ev = calculer_completude_inscription(
                ecole_id, annee_id, ins,
                periode=periode,
                periode_publiee=periode_publiee
            )
            evals_by_ins[ins.id] = ev
            if ev["status"] == STATUS_COMPLETE:
                complets.append((ins, ev))
            elif ev["status"] == STATUS_PROVISOIRE:
                provisoires.append((ins, ev))
            else:
                non_evalues.append((ins, ev))

    # Classement : UNIQUEMENT les élèves complets (période publiée + 100% matières)
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
        "evals_by_ins": evals_by_ins,
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


def verifier_eligibilite_publication_periode(ecole_id, annee_id, periode_nom):
    """
    Vérifie rigoureusement pour la période donnée que TOUS les élèves inscrits
    dans chaque classe de l'année possèdent au moins une note valide dans
    toutes leurs matières attendues (complétude par élève et par matière).

    Retourne un dictionnaire :
    {
        "eligible": bool,
        "total_incomplets": int,
        "classes_concernees": list[str],
        "details_par_classe": dict[str, list[dict]],
        "message_resume": str,
    }
    """
    if not ecole_id or not annee_id or not periode_nom:
        return {
            "eligible": False,
            "total_incomplets": 0,
            "classes_concernees": [],
            "details_par_classe": {},
            "message_resume": "Paramètres d'école ou d'année invalides.",
        }

    classes = classes_triees_pedagogique(
        Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_id)
    ).all()

    details_par_classe = defaultdict(list)
    total_incomplets = 0

    for classe in classes:
        cours_attendus = get_cours_attendus_classe(ecole_id, classe.id, annee_id)
        if not cours_attendus:
            continue

        inscriptions = (
            Inscription.query.options(joinedload(Inscription.eleve))
            .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_id, classe_id=classe.id)
            .all()
        )
        if not inscriptions:
            continue

        for ins in inscriptions:
            eleve = ins.eleve
            eleve_nom = f"{eleve.prenom} {eleve.nom}".strip() if eleve else f"Élève #{ins.eleve_id}"

            ev = calculer_completude_inscription(
                ecole_id, annee_id, ins,
                periode=periode_nom,
                periode_publiee=False  # On teste la complétude pédagogique
            )

            if not ev["is_pedagogically_complete"]:
                total_incomplets += 1
                manquants = ev["missing_subjects_names"]
                details_par_classe[classe.nom].append({
                    "inscription_id": ins.id,
                    "eleve": eleve,
                    "eleve_nom": eleve_nom,
                    "manquants": manquants,
                })

    classes_concernees = list(details_par_classe.keys())
    eligible = (total_incomplets == 0)

    if eligible:
        message_resume = "Toutes les évaluations sont complètes. La période peut être publiée."
    else:
        resume_classes = []
        for c_nom in classes_concernees[:3]:
            eleves_incomplets = details_par_classe[c_nom]
            desc_eleves = []
            for el in eleves_incomplets[:3]:
                mats = ", ".join(el["manquants"][:2])
                if len(el["manquants"]) > 2:
                    mats += f" +{len(el['manquants']) - 2}"
                desc_eleves.append(f"{el['eleve_nom']} ({mats})")
            if len(eleves_incomplets) > 3:
                desc_eleves.append(f"+{len(eleves_incomplets) - 3} autre(s)")
            resume_classes.append(f"{c_nom} : " + "; ".join(desc_eleves))

        if len(classes_concernees) > 3:
            resume_classes.append(f"+{len(classes_concernees) - 3} autre(s) classe(s)")

        message_resume = (
            f"Publication impossible : {total_incomplets} bulletin(s) incomplet(s) dans {len(classes_concernees)} classe(s). "
            + " | ".join(resume_classes)
            + ". Toutes les matières obligatoires doivent être notées pour chaque élève avant publication."
        )

    return {
        "eligible": eligible,
        "total_incomplets": total_incomplets,
        "classes_concernees": classes_concernees,
        "details_par_classe": dict(details_par_classe),
        "message_resume": message_resume,
    }

