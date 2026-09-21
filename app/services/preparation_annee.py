"""
app/services/preparation_annee.py
=============================================================
KLASORA — Phase 2D-4B
Assistant central « Préparer l'année » pour toute année scolaire
ayant statut = 'planifiee'.

Fournit le diagnostic complet en 6 étapes :
  1. Structure pédagogique
  2. Classes
  3. Cours et professeurs (informatif / non bloquant)
  4. Passage des élèves (si source antérieure applicable)
  5. Vérification finale (bloquants vs avertissements, progression)
  6. Éligibilité à l'activation

Règle absolue 2C-5D :
  Ce service est en lecture seule sur les configurations et
  inscriptions. Il ne modifie jamais session["annee_consultee"].
=============================================================
"""

from app import db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Inscription,
    NiveauScolaire,
)
from app.utils_classes import classes_triees_pedagogique
from app.services.classes_annuelles import classe_est_ouverte
from app.services.passage_annee import valider_contexte_passage


def determiner_source_passage_pour_cible(cible, toutes_annees):
    """
    Détermine l'année source appropriée pour un passage vers `cible`.
    Conditions :
      - même établissement (ecole_id) ;
      - cible non archivée ;
      - source.date_debut < cible.date_debut ;
      - couple validé par valider_contexte_passage.
    """
    if not cible or getattr(cible, 'statut', None) == 'archivee':
        return None

    candidats = [
        a for a in toutes_annees
        if a.ecole_id == cible.ecole_id and a.id != cible.id and a.statut != 'archivee' and a.date_debut < cible.date_debut
    ]
    if not candidats:
        return None

    # 1. Si cible est planifiée et qu'une année active antérieure existe, la privilégier
    source_active = next((a for a in candidats if a.statut == 'active'), None)
    if source_active:
        annee_src, _, err = valider_contexte_passage(cible.ecole_id, source_active.id, cible.id)
        if not err:
            return annee_src

    # 2. Sinon (ou si cible est elle-même active), prendre l'antérieure la plus récente
    candidats_tries = sorted(candidats, key=lambda a: a.date_debut, reverse=True)
    for cand in candidats_tries:
        annee_src, _, err = valider_contexte_passage(cible.ecole_id, cand.id, cible.id)
        if not err:
            return annee_src

    return None


def get_etat_preparation_annee(ecole_id, annee_id):
    """
    Calcule et retourne l'état détaillé de préparation d'une année scolaire
    (typiquement planifiée) pour un établissement donné.

    Retourne un dictionnaire contenant :
      - 'annee': instance AnneeScolaire
      - 'structure': dict(prete, total_niveaux, niveaux, configs)
      - 'classes': dict(prete, statut_label, total_classes, ouvertes, fermees, niveaux_sans_classe, classes)
      - 'cours': dict(prete, total_cours, avec_prof, sans_prof, classes_sans_cours, cours)
      - 'passage': dict(applicable, source, total_eleves, traites, a_traiter, prete)
      - 'verification': dict(bloquants, avertissements, prete_pour_activation)
      - 'progression': int (0-100)
    """
    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        return None

    # -------------------------------------------------------------
    # 1. Structure pédagogique
    # -------------------------------------------------------------
    # Niveaux configurés comme actifs spécifiquement pour cette année
    configs = (
        AnneeNiveauConfig.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id, actif=True)
        .join(NiveauScolaire, NiveauScolaire.id == AnneeNiveauConfig.niveau_id)
        .order_by(NiveauScolaire.ordre.asc())
        .all()
    )
    niveaux_actifs = [c.niveau for c in configs if c.niveau]
    structure_prete = len(niveaux_actifs) > 0

    structure_data = {
        "prete": structure_prete,
        "total_niveaux": len(niveaux_actifs),
        "niveaux": niveaux_actifs,
        "configs": configs,
    }

    # -------------------------------------------------------------
    # 2. Classes
    # -------------------------------------------------------------
    classes_annee = classes_triees_pedagogique(
        Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
    ).all()
    classes_ouvertes = [c for c in classes_annee if classe_est_ouverte(c)]
    classes_fermees = [c for c in classes_annee if not classe_est_ouverte(c)]

    niveaux_avec_classe_ouverte_ids = {
        c.niveau_id for c in classes_ouvertes if c.niveau_id is not None
    }
    niveaux_sans_classe = [
        n for n in niveaux_actifs
        if n.id not in niveaux_avec_classe_ouverte_ids
    ]

    classes_pretes = (
        structure_prete
        and len(classes_annee) > 0
        and len(classes_ouvertes) > 0
        and len(niveaux_sans_classe) == 0
    )

    classes_data = {
        "prete": classes_pretes,
        "statut_label": "Prête" if classes_pretes else "Incomplète",
        "total_classes": len(classes_annee),
        "ouvertes": len(classes_ouvertes),
        "fermees": len(classes_fermees),
        "niveaux_sans_classe": niveaux_sans_classe,
        "classes": classes_annee,
    }

    # -------------------------------------------------------------
    # 3. Cours et professeurs (informatif / non bloquant)
    # -------------------------------------------------------------
    classe_ids = [c.id for c in classes_annee]
    if classe_ids:
        cours_annee = (
            Cours.query
            .filter(Cours.classe_id.in_(classe_ids))
            .order_by(Cours.nom.asc())
            .all()
        )
    else:
        cours_annee = []

    cours_avec_prof = [c for c in cours_annee if c.professeur_id is not None]
    cours_sans_prof = [c for c in cours_annee if c.professeur_id is None]

    classes_avec_cours_ids = {c.classe_id for c in cours_annee if c.classe_id is not None}
    classes_sans_cours = [
        c for c in classes_ouvertes
        if c.id not in classes_avec_cours_ids
    ]

    cours_data = {
        "prete": (len(cours_annee) > 0 and len(cours_sans_prof) == 0),
        "total_cours": len(cours_annee),
        "avec_prof": len(cours_avec_prof),
        "sans_prof": len(cours_sans_prof),
        "classes_sans_cours": classes_sans_cours,
        "cours": cours_annee,
    }

    # -------------------------------------------------------------
    # 4. Passage des élèves
    # -------------------------------------------------------------
    toutes_annees_ecole = AnneeScolaire.query.filter_by(ecole_id=ecole_id).all()
    source_passage = determiner_source_passage_pour_cible(annee, toutes_annees_ecole)

    if not source_passage:
        passage_data = {
            "applicable": False,
            "source": None,
            "total_eleves": 0,
            "traites": 0,
            "a_traiter": 0,
            "prete": True,  # Non applicable ne bloque pas l'activation
        }
    else:
        inscriptions_source = (
            Inscription.query
            .filter_by(ecole_id=ecole_id, annee_scolaire_id=source_passage.id)
            .join(Eleve, Eleve.id == Inscription.eleve_id)
            .all()
        )
        total_eleves_source = len(inscriptions_source)

        inscriptions_cible_map = {
            insc.eleve_id: insc
            for insc in Inscription.query
            .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
            .all()
        }

        nb_traites = 0
        for insc_src in inscriptions_source:
            if insc_src.eleve_id in inscriptions_cible_map:
                nb_traites += 1
            elif insc_src.decision_fin_annee in ("transfert", "sortie", "diplome"):
                nb_traites += 1
            elif insc_src.statut in ("transfere", "sorti", "diplome"):
                nb_traites += 1

        nb_a_traiter = total_eleves_source - nb_traites
        passage_prete = (nb_a_traiter == 0)

        passage_data = {
            "applicable": True,
            "source": source_passage,
            "total_eleves": total_eleves_source,
            "traites": nb_traites,
            "a_traiter": nb_a_traiter,
            "prete": passage_prete,
        }

    # -------------------------------------------------------------
    # 5. Vérification finale (Bloquants vs Avertissements)
    # -------------------------------------------------------------
    bloquants = []
    avertissements = []

    # Bloquants - Structure
    if not structure_data["prete"]:
        bloquants.append(
            "Structure pédagogique non configurée : aucun niveau actif n'est sélectionné pour cette année."
        )

    # Bloquants - Classes
    if structure_data["prete"]:
        if classes_data["total_classes"] == 0:
            bloquants.append("Aucune classe n'a été créée pour cette année scolaire.")
        elif classes_data["ouvertes"] == 0:
            bloquants.append("Aucune classe n'est ouverte pour cette année scolaire.")
        elif len(classes_data["niveaux_sans_classe"]) > 0:
            noms_niveaux = ", ".join(n.nom for n in classes_data["niveaux_sans_classe"])
            bloquants.append(
                f"{len(classes_data['niveaux_sans_classe'])} niveau(x) actif(s) n'ont aucune classe ouverte ({noms_niveaux})."
            )

    # Bloquants - Passage des élèves (si applicable)
    if passage_data["applicable"] and passage_data["a_traiter"] > 0:
        bloquants.append(
            f"{passage_data['a_traiter']} élève(s) de l'année précédente ({passage_data['source'].nom}) n'ont pas encore été traités dans le passage d'année."
        )

    # Avertissements (Non bloquants)
    if cours_data["total_cours"] == 0:
        avertissements.append("Aucun cours n'a été créé pour cette année scolaire.")
    else:
        if cours_data["sans_prof"] > 0:
            avertissements.append(
                f"{cours_data['sans_prof']} cours n'ont aucun enseignant assigné."
            )
        if len(cours_data["classes_sans_cours"]) > 0:
            noms_classes = ", ".join(c.nom for c in cours_data["classes_sans_cours"])
            avertissements.append(
                f"{len(cours_data['classes_sans_cours'])} classe(s) ouverte(s) n'ont aucun cours configuré ({noms_classes})."
            )

    prete_pour_activation = (len(bloquants) == 0)

    # -------------------------------------------------------------
    # Calcul du score de progression (0 à 100%)
    # -------------------------------------------------------------
    if prete_pour_activation:
        progression = 100
    else:
        # Pondération selon si le passage est applicable ou non
        if passage_data["applicable"]:
            # 3 étapes requises : Structure (30%), Classes (40%), Passage (30%)
            score_struct = 30 if structure_data["prete"] else 0

            if structure_data["total_niveaux"] > 0 and classes_data["ouvertes"] > 0:
                niveaux_avec_classe = structure_data["total_niveaux"] - len(classes_data["niveaux_sans_classe"])
                ratio_classes = max(0.0, min(1.0, niveaux_avec_classe / structure_data["total_niveaux"]))
                score_classes = int(40 * ratio_classes)
            else:
                score_classes = 0

            if passage_data["total_eleves"] > 0:
                ratio_passage = max(0.0, min(1.0, passage_data["traites"] / passage_data["total_eleves"]))
                score_passage = int(30 * ratio_passage)
            else:
                score_passage = 30

            progression = score_struct + score_classes + score_passage
        else:
            # 2 étapes requises : Structure (40%), Classes (60%)
            score_struct = 40 if structure_data["prete"] else 0

            if structure_data["total_niveaux"] > 0 and classes_data["ouvertes"] > 0:
                niveaux_avec_classe = structure_data["total_niveaux"] - len(classes_data["niveaux_sans_classe"])
                ratio_classes = max(0.0, min(1.0, niveaux_avec_classe / structure_data["total_niveaux"]))
                score_classes = int(60 * ratio_classes)
            else:
                score_classes = 0

            progression = score_struct + score_classes

        # Ne jamais afficher 100% tant qu'il reste un bloquant
        progression = min(progression, 95)

    verification_data = {
        "bloquants": bloquants,
        "avertissements": avertissements,
        "prete_pour_activation": prete_pour_activation,
    }

    return {
        "annee": annee,
        "structure": structure_data,
        "classes": classes_data,
        "cours": cours_data,
        "passage": passage_data,
        "verification": verification_data,
        "progression": progression,
    }

