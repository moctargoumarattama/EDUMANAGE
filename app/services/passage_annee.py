"""
app/services/passage_annee.py
=============================================================
KLASORA — Phase 2D-2
Service de passage d'année individuel et atomique.

Responsabilités :
  - Valider le contexte source / cible
  - Déterminer le niveau cible selon la décision
  - Lister les classes candidates (lecture seule)
  - Préparer le passage sans mutation (audit pré-exécution)
  - Exécuter le passage de façon atomique

Décisions supportées :
  passage        → nouvelle Inscription cible (niveau suivant)
  redoublement   → nouvelle Inscription cible (même niveau)
  transfert      → clôture source uniquement (statut = transfere)
  sortie         → clôture source uniquement (statut = sorti)
  diplome        → clôture source uniquement (statut = diplome)
  fin_cycle      → clôture source uniquement (statut = termine)
                   Aucune Inscription cible automatique.
                   Sémantique réservée aux passages de cycle
                   inter-établissements gérés manuellement.

Règle absolue :
  Une Inscription historique ne change JAMAIS d'année.
  Toute nouvelle situation annuelle = NOUVELLE Inscription.

Ce module ne définit aucune route Flask.
=============================================================
"""

from datetime import datetime

from app import db
from app.models import AnneeScolaire, Bulletin, Classe, Cours, Eleve, Inscription, Note
from sqlalchemy import func
from app.utils_classes import classes_triees_pedagogique
from app.services.classes_annuelles import classe_est_ouverte
from app.services.inscriptions_annuelles import (
    DECISIONS_FIN_ANNEE,
    STATUTS_INSCRIPTION,
    creer_inscription_annuelle,
    get_inscription,
    terminer_inscription,
)
from app.services.niveaux_annuels import niveau_actif_pour_annee
from app.services.niveaux import niveau_peut_etre_utilise


# ---------------------------------------------------------------------------
# Constantes de décision
# ---------------------------------------------------------------------------

DECISIONS_AVEC_CIBLE   = {"passage", "redoublement"}
DECISIONS_SANS_CIBLE   = {"transfert", "sortie", "diplome", "fin_cycle"}
TOUTES_DECISIONS       = DECISIONS_AVEC_CIBLE | DECISIONS_SANS_CIBLE

# Correspondance décision → statut sur l'inscription source
_STATUT_SOURCE = {
    "passage":      "termine",
    "redoublement": "termine",
    "transfert":    "transfere",
    "sortie":       "sorti",
    "diplome":      "diplome",
    "fin_cycle":    "termine",
}


# ---------------------------------------------------------------------------
# 1. Validation du contexte source / cible
# ---------------------------------------------------------------------------

def valider_contexte_passage(ecole_id, annee_source_id, annee_cible_id):
    """
    Valide que la paire (source, cible) est cohérente pour un passage d'année.

    Règles vérifiées :
      - les deux années existent et appartiennent à ecole_id ;
      - source != cible ;
      - source.date_debut < cible.date_debut  (ordre chronologique réel) ;
      - cible.statut != 'archivee'.

    Retourne : (annee_source, annee_cible, error_str | None)
    """
    annee_source = AnneeScolaire.query.filter_by(
        id=annee_source_id, ecole_id=ecole_id
    ).first()
    if not annee_source:
        return None, None, "Année source introuvable pour cet établissement."

    annee_cible = AnneeScolaire.query.filter_by(
        id=annee_cible_id, ecole_id=ecole_id
    ).first()
    if not annee_cible:
        return None, None, "Année cible introuvable pour cet établissement."

    if annee_source.id == annee_cible.id:
        return None, None, "L'année source et l'année cible doivent être différentes."

    if annee_source.date_debut >= annee_cible.date_debut:
        return None, None, (
            "L'année source doit être chronologiquement antérieure à l'année cible."
        )

    if annee_cible.statut == "archivee":
        return None, None, "Impossible d'inscrire dans une année cible archivée."

    return annee_source, annee_cible, None


# ---------------------------------------------------------------------------
# 2. Niveau cible selon la décision
# ---------------------------------------------------------------------------

def _determiner_niveau_cible(classe_source, decision):
    """
    Retourne (niveau_cible, error_str | None).

    passage      → niveau_suivant du niveau source (None si Terminale)
    redoublement → niveau source inchangé
    autres       → (None, None)  — pas de niveau cible attendu
    """
    if decision not in DECISIONS_AVEC_CIBLE:
        return None, None

    niveau_source = classe_source.niveau_scolaire if classe_source else None

    if not niveau_source:
        return None, (
            "La classe source ne possède pas de niveau scolaire référencé. "
            "Correction manuelle requise (niveau_id manquant — legacy)."
        )

    if decision == "passage":
        niveau_suivant = niveau_source.niveau_suivant
        if niveau_suivant is None:
            return None, (
                f"Le niveau {niveau_source.nom} n'a pas de niveau suivant "
                "(ex. Terminale). Utiliser la décision 'diplome', 'sortie' "
                "ou 'transfert'."
            )
        return niveau_suivant, None

    if decision == "redoublement":
        return niveau_source, None

    return None, None


# ---------------------------------------------------------------------------
# 3. Classes candidates pour une décision donnée
# ---------------------------------------------------------------------------

def get_classes_candidates_passage(ecole_id, annee_cible_id, niveau_id):
    """
    Retourne la liste des classes ouvertes du niveau_id donné
    dans l'année cible, en respectant AnneeNiveauConfig.

    Règles :
      - même école ;
      - annee_scolaire_id == annee_cible_id ;
      - statut == 'ouverte' ;
      - niveau_id correspondant ;
      - niveau actif dans AnneeNiveauConfig pour l'année cible.

    Retourne : list[Classe]  (peut être vide si aucune classe candidate)
    """
    if not niveau_actif_pour_annee(ecole_id, annee_cible_id, niveau_id):
        return []

    classes = classes_triees_pedagogique(
        Classe.query
        .filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee_cible_id,
            niveau_id=niveau_id,
            statut="ouverte",
        )
    ).all()
    return classes


# ---------------------------------------------------------------------------
# 4. Préparation (lecture seule — aucune mutation)
# ---------------------------------------------------------------------------

def preparer_passage_eleve(ecole_id, eleve_id, annee_source_id, annee_cible_id, decision):
    """
    Prépare les informations nécessaires à un passage d'année sans rien modifier.

    Retourne un dict avec :
      ok                  bool
      eleve               Eleve | None
      inscription_source  Inscription | None
      classe_source       Classe | None
      niveau_source       NiveauScolaire | None
      niveau_cible        NiveauScolaire | None
      classes_candidates  list[Classe]
      deja_inscrit_cible  bool
      inscription_cible_existante  Inscription | None
      decision            str
      blocage             str | None  — message d'erreur si non exécutable

    Cette fonction NE MODIFIE RIEN EN BASE.
    """

    result = {
        "ok": False,
        "eleve": None,
        "inscription_source": None,
        "classe_source": None,
        "niveau_source": None,
        "niveau_cible": None,
        "classes_candidates": [],
        "deja_inscrit_cible": False,
        "inscription_cible_existante": None,
        "decision": decision,
        "blocage": None,
    }

    # --- Décision valide 
    if decision not in TOUTES_DECISIONS:
        result["blocage"] = f"Décision inconnue : '{decision}'."
        return result

    # --- Contexte années
    annee_source, annee_cible, error = valider_contexte_passage(
        ecole_id, annee_source_id, annee_cible_id
    )
    if error:
        result["blocage"] = error
        return result

    # --- Élève
    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        result["blocage"] = "Élève introuvable pour cet établissement."
        return result
    result["eleve"] = eleve

    # --- Inscription source
    inscription_source = get_inscription(eleve, annee_source)
    if not inscription_source:
        result["blocage"] = (
            "Aucune inscription trouvée pour cet élève dans l'année source."
        )
        return result
    result["inscription_source"] = inscription_source

    # --- Classe et niveau source
    classe_source = inscription_source.classe
    result["classe_source"] = classe_source
    if classe_source:
        result["niveau_source"] = classe_source.niveau_scolaire

    # --- Vérification inscription cible existante
    inscription_cible_existante = get_inscription(eleve, annee_cible)
    if inscription_cible_existante:
        result["deja_inscrit_cible"] = True
        result["inscription_cible_existante"] = inscription_cible_existante

    # --- Niveau cible et classes candidates
    if decision in DECISIONS_AVEC_CIBLE:
        niveau_cible, error = _determiner_niveau_cible(classe_source, decision)
        if error:
            result["blocage"] = error
            return result
        result["niveau_cible"] = niveau_cible

        if niveau_cible:
            result["classes_candidates"] = get_classes_candidates_passage(
                ecole_id, annee_cible_id, niveau_cible.id
            )

    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# 5. Exécution atomique
# ---------------------------------------------------------------------------

def executer_passage_eleve(
    ecole_id,
    eleve_id,
    annee_source_id,
    annee_cible_id,
    decision,
    classe_cible_id=None,
    motif_sortie=None,
    statut_cible=None,
    date_depart=None,
    etablissement_destination=None,
):
    """
    Exécute atomiquement le passage d'année pour un élève.

    Stratégie :
      1. Toutes les validations d'abord, aucune mutation.
      2. Créer l'Inscription cible (si nécessaire).
      3. Clôturer l'Inscription source.
      4. db.session.flush() — le commit est laissé à l'appelant
         pour permettre les transactions englobantes.

    Retourne : (result_dict, error_str | None)

    result_dict :
      ok                   bool
      action               str  (décision)
      eleve_id             int
      inscription_source_id int
      inscription_cible_id  int | None
      classe_cible_id       int | None
      deja_traite          bool

    ATOMICITÉ :
      Si une étape de mutation échoue après que d'autres ont réussi,
      les changements en session sont annulés par l'appelant via rollback.
      Ce service utilise uniquement flush() — pas commit() —
      pour laisser la transaction ouverte à l'appelant.
    """

    # -----------------------------------------------------------------------
    # PHASE 1 : Toutes les validations avant toute mutation
    # -----------------------------------------------------------------------

    if decision not in TOUTES_DECISIONS:
        return None, f"Décision inconnue : '{decision}'."

    # --- Contexte années
    annee_source, annee_cible, error = valider_contexte_passage(
        ecole_id, annee_source_id, annee_cible_id
    )
    if error:
        return None, error

    # --- Source archivée → refus de mutation
    if annee_source.statut == "archivee":
        return None, (
            "L'année source est archivée. "
            "Une inscription archivée ne peut pas être modifiée."
        )

    # --- Élève
    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        return None, "Élève introuvable pour cet établissement."

    # --- Inscription source
    inscription_source = get_inscription(eleve, annee_source)
    if not inscription_source:
        return None, (
            "Aucune inscription trouvée pour cet élève dans l'année source."
        )

    # --- Classe et niveau source
    classe_source = inscription_source.classe
    niveau_source = classe_source.niveau_scolaire if classe_source else None

    # --- Vérifications spécifiques par décision
    if decision in DECISIONS_AVEC_CIBLE:

        # classe_cible_id obligatoire
        if not classe_cible_id:
            return None, (
                f"La décision '{decision}' requiert une classe cible explicite."
            )

        # Charger et valider la classe cible
        classe_cible = Classe.query.filter_by(
            id=classe_cible_id, ecole_id=ecole_id
        ).first()
        if not classe_cible:
            return None, "Classe cible introuvable pour cet établissement."
        if classe_cible.annee_scolaire_id != annee_cible.id:
            return None, "La classe cible n'appartient pas à l'année cible."
        if not classe_est_ouverte(classe_cible):
            return None, "La classe cible est fermée."

        # Niveau cible attendu
        niveau_cible, error = _determiner_niveau_cible(classe_source, decision)
        if error:
            return None, error

        # Vérifier que la classe cible est au niveau attendu (anti-forgerie POST)
        if niveau_cible and classe_cible.niveau_id != niveau_cible.id:
            return None, (
                f"La classe cible ne correspond pas au niveau attendu "
                f"({niveau_cible.nom}) pour la décision '{decision}'."
            )

        # Niveau cible actif dans AnneeNiveauConfig 
        if niveau_cible and not niveau_actif_pour_annee(
            ecole_id, annee_cible.id, niveau_cible.id
        ):
            return None, (
                f"Le niveau {niveau_cible.nom} n'est pas retenu "
                "pour l'année cible."
            )

    elif decision in DECISIONS_SANS_CIBLE:

        # classe_cible_id interdit pour ces décisions
        if classe_cible_id:
            return None, (
                f"La décision '{decision}' ne doit pas recevoir de classe cible."
            )
        classe_cible = None
        niveau_cible = None

        # Règle métier : diplome doit être cohérent avec Terminale
        if decision == "diplome":
            if niveau_source is None:
                # Legacy sans niveau_id : on autorise mais on ne valide pas
                pass
            else:
                # Si le niveau n'est PAS Terminale et n'a pas niveau_suivant==None
                # → refus (6e → diplome est invalide)
                if niveau_source.niveau_suivant is not None:
                    return None, (
                        f"La décision 'diplome' n'est pas valide pour le niveau "
                        f"{niveau_source.nom}. "
                        "Elle est réservée aux élèves en fin de cycle terminal "
                        "(ex. Terminale)."
                    )

    # --- Inscription cible déjà existante 
    inscription_cible_existante = get_inscription(eleve, annee_cible)

    if inscription_cible_existante:
        if decision in DECISIONS_AVEC_CIBLE:
            # Idempotence : même classe 
            if inscription_cible_existante.classe_id == classe_cible_id:
                return {
                    "ok": True,
                    "action": decision,
                    "eleve_id": eleve.id,
                    "inscription_source_id": inscription_source.id,
                    "inscription_cible_id": inscription_cible_existante.id,
                    "classe_cible_id": inscription_cible_existante.classe_id,
                    "deja_traite": True,
                }, None
            # Conflit : classe différente
            return None, (
                "Un conflit existe : l'élève est déjà inscrit dans l'année cible "
                f"dans une classe différente (classe_id={inscription_cible_existante.classe_id})."
            )
        if decision in DECISIONS_SANS_CIBLE:
            # Si l'élève a une préinscription/inscription dans l'année cible,
            # un transfert ou une radiation doit annuler cette inscription cible
            # et ne jamais bloquer l'opération avec "déjà traité".
            if inscription_cible_existante and inscription_cible_existante.statut != "annulee":
                inscription_cible_existante.statut = "annulee"
                inscription_cible_existante.motif_sortie = f"Annulé suite à décision {decision}"
                inscription_cible_existante.updated_at = datetime.utcnow()
                db.session.flush()

            # Déjà traité uniquement si l'inscription source est DÉJÀ clôturée sous ce statut
            statut_attendu = _STATUT_SOURCE.get(decision, "sorti")
            if inscription_source.statut == statut_attendu and inscription_source.decision_fin_annee == decision:
                return {
                    "ok": True,
                    "action": decision,
                    "eleve_id": eleve.id,
                    "inscription_source_id": inscription_source.id,
                    "inscription_cible_id": None,
                    "classe_cible_id": None,
                    "deja_traite": True,
                }, None

    # -----------------------------------------------------------------------
    # PHASE 2 : Mutations atomiques
    # Toutes les validations ont réussi — on peut muter.
    # -----------------------------------------------------------------------

    inscription_cible = None

    try:
        # 2a. Créer l'Inscription cible (si nécessaire)
        if decision in DECISIONS_AVEC_CIBLE:
            # sync_active=True uniquement si l'année cible est active
            sync = (annee_cible.statut == "active")
            if statut_cible is None:
                statut_cible = "preinscrit" if annee_cible.statut == "planifiee" else "inscrit"
            inscription_cible, error = creer_inscription_annuelle(
                ecole_id=ecole_id,
                eleve_id=eleve.id,
                annee_scolaire_id=annee_cible.id,
                classe_id=classe_cible.id,
                statut=statut_cible,
                sync_active=sync,
            )
            if error:
                return None, f"Erreur création inscription cible : {error}"

        # 2b. Clôturer l'Inscription source
        statut_source = _STATUT_SOURCE[decision]
        _, error = terminer_inscription(
            inscription=inscription_source,
            decision_fin_annee=decision,
            statut=statut_source,
            motif_sortie=motif_sortie if decision in DECISIONS_SANS_CIBLE else None,
        )
        if error:
            return None, f"Erreur clôture inscription source : {error}"

        if date_depart:
            inscription_source.date_depart = date_depart
        if etablissement_destination:
            inscription_source.etablissement_destination = etablissement_destination
        if decision in ("transfert", "sortie"):
            eleve.statut = statut_source

        # 2c. Flush groupé — le commit appartient à l'appelant
        db.session.flush()

    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        return None, f"Erreur inattendue lors du passage — rollback effectué : {exc}"

    return {
        "ok": True,
        "succes": True,
        "action": decision,
        "eleve_id": eleve.id,
        "inscription_source_id": inscription_source.id,
        "inscription_cible_id": inscription_cible.id if inscription_cible else None,
        "classe_cible_id": classe_cible.id if (decision in DECISIONS_AVEC_CIBLE and classe_cible) else None,
        "deja_traite": False,
    }, None


# ---------------------------------------------------------------------------
# 6. Traitement en masse (Phase 2D-4)
# ---------------------------------------------------------------------------

def preparer_passage_masse(
    ecole_id,
    annee_source_id,
    annee_cible_id,
    eleve_ids,
    decision,
    classe_cible_id=None,
    motif_sortie=None,
):
    """
    Prépare et audite le passage d'année pour un lot d'élèves, SANS RIEN MODIFIER EN BASE.

    Retourne un dict :
      ok: bool (True si les paramètres globaux sont valides)
      error: str | None (message d'erreur global si ok=False)
      annee_source: AnneeScolaire | None
      annee_cible: AnneeScolaire | None
      classe_cible: Classe | None
      decision: str
      motif_sortie: str | None
      total: int
      nb_valides: int
      nb_deja_traites: int
      nb_conflits: int
      items: list[dict]
    """
    result = {
        "ok": False,
        "error": None,
        "annee_source": None,
        "annee_cible": None,
        "classe_cible": None,
        "decision": decision,
        "motif_sortie": motif_sortie,
        "total": 0,
        "nb_valides": 0,
        "nb_deja_traites": 0,
        "nb_conflits": 0,
        "items": [],
    }

    if not eleve_ids:
        result["error"] = "Aucun élève sélectionné."
        return result

    if decision not in TOUTES_DECISIONS:
        result["error"] = f"Décision inconnue : '{decision}'."
        return result

    annee_source, annee_cible, error = valider_contexte_passage(
        ecole_id, annee_source_id, annee_cible_id
    )
    if error:
        result["error"] = error
        return result

    result["annee_source"] = annee_source
    result["annee_cible"] = annee_cible

    if annee_source.statut == "archivee":
        result["error"] = (
            "L'année source est archivée. "
            "Une inscription archivée ne peut pas être modifiée."
        )
        return result

    classe_cible = None
    if decision in DECISIONS_AVEC_CIBLE:
        if not classe_cible_id:
            result["error"] = f"La décision '{decision}' requiert une classe cible explicite."
            return result

        classe_cible = Classe.query.filter_by(
            id=classe_cible_id, ecole_id=ecole_id
        ).first()
        if not classe_cible:
            result["error"] = "Classe cible introuvable pour cet établissement."
            return result
        if classe_cible.annee_scolaire_id != annee_cible.id:
            result["error"] = "La classe cible n'appartient pas à l'année cible."
            return result
        if not classe_est_ouverte(classe_cible):
            result["error"] = "La classe cible est fermée."
            return result

        result["classe_cible"] = classe_cible

    # Déduplication ordonnée des eleve_ids
    seen = set()
    eleve_ids_uniques = []
    for eid in eleve_ids:
        if eid not in seen:
            seen.add(eid)
            eleve_ids_uniques.append(eid)

    items = []
    for eid in eleve_ids_uniques:
        eleve = Eleve.query.filter_by(id=eid, ecole_id=ecole_id).first()
        if not eleve:
            items.append({
                "eleve_id": eid,
                "eleve": None,
                "inscription_source": None,
                "classe_source": None,
                "statut": "conflit",
                "statut_label": "Introuvable",
                "motif": "Élève introuvable pour cet établissement.",
            })
            continue

        prep = preparer_passage_eleve(
            ecole_id=ecole_id,
            eleve_id=eleve.id,
            annee_source_id=annee_source.id,
            annee_cible_id=annee_cible.id,
            decision=decision,
        )

        insc_source = prep.get("inscription_source")
        classe_src = prep.get("classe_source")

        if prep.get("blocage"):
            items.append({
                "eleve_id": eleve.id,
                "eleve": eleve,
                "inscription_source": insc_source,
                "classe_source": classe_src,
                "statut": "conflit",
                "statut_label": "Non éligible",
                "motif": prep["blocage"],
            })
            continue

        # Inscription cible déjà existante 
        insc_cible_existante = prep.get("inscription_cible_existante")

        if decision in DECISIONS_AVEC_CIBLE:
            # Vérifier la cohérence de niveau
            niveau_attendu = prep.get("niveau_cible")
            if niveau_attendu and classe_cible.niveau_id != niveau_attendu.id:
                items.append({
                    "eleve_id": eleve.id,
                    "eleve": eleve,
                    "inscription_source": insc_source,
                    "classe_source": classe_src,
                    "statut": "conflit",
                    "statut_label": "Conflit",
                    "motif": (
                        f"La classe cible {classe_cible.nom} "
                        f"({classe_cible.niveau_scolaire.nom if classe_cible.niveau_scolaire else 'sans niveau'}) "
                        f"ne correspond pas au niveau attendu ({niveau_attendu.nom})."
                    ),
                })
                continue

            if insc_cible_existante:
                if insc_cible_existante.classe_id == classe_cible.id:
                    items.append({
                        "eleve_id": eleve.id,
                        "eleve": eleve,
                        "inscription_source": insc_source,
                        "classe_source": classe_src,
                        "statut": "deja_traite",
                        "statut_label": "Déjà traité",
                        "motif": f"Déjà inscrit dans la classe {classe_cible.nom}.",
                    })
                else:
                    nom_autre = (
                        insc_cible_existante.classe.nom
                        if insc_cible_existante.classe
                        else f"classe #{insc_cible_existante.classe_id}"
                    )
                    items.append({
                        "eleve_id": eleve.id,
                        "eleve": eleve,
                        "inscription_source": insc_source,
                        "classe_source": classe_src,
                        "statut": "conflit",
                        "statut_label": "Conflit",
                        "motif": f"Déjà inscrit dans une autre classe ({nom_autre}) pour l'année cible.",
                    })
                continue

            # Tout est valide pour décision avec cible
            items.append({
                "eleve_id": eleve.id,
                "eleve": eleve,
                "inscription_source": insc_source,
                "classe_source": classe_src,
                "statut": "valide",
                "statut_label": "Prêt",
                "motif": None,
            })

        else:
            # DECISIONS_SANS_CIBLE
            if decision == "diplome":
                niveau_src = prep.get("niveau_source")
                if niveau_src and niveau_src.niveau_suivant is not None:
                    items.append({
                        "eleve_id": eleve.id,
                        "eleve": eleve,
                        "inscription_source": insc_source,
                        "classe_source": classe_src,
                        "statut": "conflit",
                        "statut_label": "Non éligible",
                        "motif": (
                            f"La décision 'diplome' n'est pas autorisée pour le niveau "
                            f"{niveau_src.nom} (réservée au cycle terminal)."
                        ),
                    })
                    continue

                if insc_source and (
                    insc_source.decision_fin_annee == "diplome"
                    or insc_source.statut == "diplome"
                ):
                    items.append({
                        "eleve_id": eleve.id,
                        "eleve": eleve,
                        "inscription_source": insc_source,
                        "classe_source": classe_src,
                        "statut": "deja_traite",
                        "statut_label": "Déjà traité",
                        "motif": "Élève déjà diplômé.",
                    })
                    continue

            elif decision in ("transfert", "sortie"):
                if insc_source and (
                    insc_source.decision_fin_annee == decision
                    or insc_source.statut in ("transfere", "sorti")
                ):
                    items.append({
                        "eleve_id": eleve.id,
                        "eleve": eleve,
                        "inscription_source": insc_source,
                        "classe_source": classe_src,
                        "statut": "deja_traite",
                        "statut_label": "Déjà traité",
                        "motif": f"Élève déjà enregistré comme {insc_source.statut}.",
                    })
                    continue

            # Cas valide sans cible
            items.append({
                "eleve_id": eleve.id,
                "eleve": eleve,
                "inscription_source": insc_source,
                "classe_source": classe_src,
                "statut": "valide",
                "statut_label": "Prêt",
                "motif": None,
            })

    result["ok"] = True
    result["items"] = items
    result["total"] = len(items)
    result["nb_valides"] = sum(1 for it in items if it["statut"] == "valide")
    result["nb_deja_traites"] = sum(1 for it in items if it["statut"] == "deja_traite")
    result["nb_conflits"] = sum(1 for it in items if it["statut"] == "conflit")
    return result


def executer_passage_masse(
    ecole_id,
    annee_source_id,
    annee_cible_id,
    eleve_ids,
    decision,
    classe_cible_id=None,
    motif_sortie=None,
):
    """
    Exécute atomiquement par élève (savepoint SQLAlchemy) le passage d'année en masse.

    Pour chaque élève :
      - Ouvre un savepoint : savepoint = db.session.begin_nested()
      - Tente executer_passage_eleve(...)
      - Si erreur : savepoint.rollback(), enregistre l'échec/conflit
      - Si deja_traite : savepoint.rollback(), enregistre déjà traité
      - Si succès : savepoint.commit(), enregistre le succès

    À la fin de la boucle, fait db.session.commit() pour persister l'ensemble des élèves réussis.

    Retourne : (rapport_dict, error_str | None)
    """
    if not eleve_ids:
        return None, "Aucun élève sélectionné."

    if decision not in TOUTES_DECISIONS:
        return None, f"Décision inconnue : '{decision}'."

    annee_source, annee_cible, error = valider_contexte_passage(
        ecole_id, annee_source_id, annee_cible_id
    )
    if error:
        return None, error

    if annee_source.statut == "archivee":
        return None, (
            "L'année source est archivée. "
            "Une inscription archivée ne peut pas être modifiée."
        )

    classe_cible = None
    classe_cible_nom = None
    if decision in DECISIONS_AVEC_CIBLE:
        if not classe_cible_id:
            return None, f"La décision '{decision}' requiert une classe cible explicite."

        classe_cible = Classe.query.filter_by(
            id=classe_cible_id, ecole_id=ecole_id
        ).first()
        if not classe_cible:
            return None, "Classe cible introuvable pour cet établissement."
        if classe_cible.annee_scolaire_id != annee_cible.id:
            return None, "La classe cible n'appartient pas à l'année cible."
        if not classe_est_ouverte(classe_cible):
            return None, "La classe cible est fermée."

        classe_cible_nom = classe_cible.nom

    # Déduplication ordonnée
    seen = set()
    eleve_ids_uniques = []
    for eid in eleve_ids:
        if eid not in seen:
            seen.add(eid)
            eleve_ids_uniques.append(eid)

    details = []
    nb_reussis = 0
    nb_deja_traites = 0
    nb_conflits = 0
    nb_echecs = 0

    for eid in eleve_ids_uniques:
        eleve = Eleve.query.filter_by(id=eid, ecole_id=ecole_id).first()
        eleve_nom = f"{eleve.nom} {eleve.prenom}" if eleve else f"Élève #{eid}"

        savepoint = db.session.begin_nested()
        try:
            res, err = executer_passage_eleve(
                ecole_id=ecole_id,
                eleve_id=eid,
                annee_source_id=annee_source_id,
                annee_cible_id=annee_cible_id,
                decision=decision,
                classe_cible_id=classe_cible_id,
                motif_sortie=motif_sortie,
            )

            if err:
                savepoint.rollback()
                is_conflit = (
                    "conflit" in err.lower()
                    or "niveau" in err.lower()
                    or "fermée" in err.lower()
                    or "archivée" in err.lower()
                )
                statut_code = "conflit" if is_conflit else "erreur"
                if is_conflit:
                    nb_conflits += 1
                else:
                    nb_echecs += 1

                details.append({
                    "eleve_id": eid,
                    "eleve_nom": eleve_nom,
                    "statut": statut_code,
                    "message": err,
                    "classe_cible_nom": classe_cible_nom,
                })
            elif res and res.get("deja_traite"):
                savepoint.rollback()
                nb_deja_traites += 1
                details.append({
                    "eleve_id": eid,
                    "eleve_nom": eleve_nom,
                    "statut": "deja_traite",
                    "message": "Déjà traité pour l'année cible.",
                    "classe_cible_nom": classe_cible_nom,
                })
            else:
                savepoint.commit()
                nb_reussis += 1
                details.append({
                    "eleve_id": eid,
                    "eleve_nom": eleve_nom,
                    "statut": "succes",
                    "message": f"Action '{decision}' validée avec succès.",
                    "classe_cible_nom": classe_cible_nom,
                })

        except Exception as exc:  # noqa: BLE001
            savepoint.rollback()
            nb_echecs += 1
            details.append({
                "eleve_id": eid,
                "eleve_nom": eleve_nom,
                "statut": "erreur",
                "message": f"Erreur inattendue : {exc}",
                "classe_cible_nom": classe_cible_nom,
            })

    try:
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        return None, f"Erreur lors de la validation en base de données : {exc}"

    rapport = {
        "ok": True,
        "annee_source": annee_source,
        "annee_cible": annee_cible,
        "decision": decision,
        "classe_cible_nom": classe_cible_nom,
        "total": len(eleve_ids_uniques),
        "nb_reussis": nb_reussis,
        "nb_deja_traites": nb_deja_traites,
        "nb_conflits": nb_conflits,
        "nb_echecs": nb_echecs,
        "details": details,
    }
    return rapport, None


# ---------------------------------------------------------------------------
# 7. Récupération des résultats académiques annuels (Quick Wins Ergonomiques)
# ---------------------------------------------------------------------------

def get_deliberations_annuelles_eleves(ecole_id, annee_id):
    """
    Évalue le parcours annuel de chaque élève selon les règles académiques strictes :
    1. Nombre de périodes obligatoires = len(annee.periodes) (ou 2 par défaut en cycle semestriel).
    2. La moyenne annuelle est calculée en divisant par le nombre réglementaire de périodes officielles.
    3. Si une ou plusieurs périodes sont manquantes (non évaluées / non scolarisées) :
       - Statut de délibération = "Dossier Incomplet"
       - Suggestion automatique de passage bloquée = "Décision réservée au conseil (cursus incomplet)"
       - cursus_incomplet = True
    4. Si le cursus est complet :
       - Statut de délibération = "Complet"
       - Suggestion = "passage" si moyenne >= 10.0 else "redoublement"

    Retourne : dict {eleve_id: dict_deliberation}
    """
    if not ecole_id or not annee_id:
        return {}

    from collections import defaultdict
    from app.services.evaluations import (
        calculer_moyenne_matiere,
        calculer_completude_annuelle_inscription,
    )
    from app.services.notes_annuelles import (
        PERIODES_SEMESTRES, calculer_moyenne_generale_semestre, calculer_moyenne_annuelle,
    )

    deliberations = {}

    annee = db.session.get(AnneeScolaire, annee_id)
    periodes_officielles = [p.nom for p in annee.periodes] if annee and annee.periodes else []
    nb_attendues = len(periodes_officielles)

    # Une inscription annulée n'a jamais donné lieu à un parcours à délibérer.
    # Les transferts/radiations restent visibles dans l'historique scolaire.
    inscriptions = Inscription.query.filter_by(
        ecole_id=ecole_id, annee_scolaire_id=annee_id
    ).filter(Inscription.statut != "annulee").all()
    eleves_inscrits = {inscription.eleve_id for inscription in inscriptions}

    # Récupérer tous les bulletins existants pour cette année
    bulletins_rows = (
        Bulletin.query.filter(
            Bulletin.ecole_id == ecole_id,
            Bulletin.annee_scolaire_id == annee_id,
            Bulletin.moyenne_generale.isnot(None),
        ).all()
    )

    if nb_attendues == 0:
        if bulletins_rows:
            periodes_uniques = {b.periode for b in bulletins_rows if b.periode}
            nb_attendues = max(1, len(periodes_uniques))
        else:
            nb_attendues = 2  # Par défaut cycle semestriel

    bulletins_par_eleve = defaultdict(list)
    for b in bulletins_rows:
        if b.eleve_id in eleves_inscrits:
            bulletins_par_eleve[b.eleve_id].append(b)

    for eleve_id, b_list in bulletins_par_eleve.items():
        periodes_vues = {}
        for b in b_list:
            p_key = b.periode or f"periode_{len(periodes_vues)}"
            periodes_vues[p_key] = float(b.moyenne_generale)

        nb_evaluees = len(periodes_vues)
        somme_moyennes = sum(periodes_vues.values())

        if nb_attendues > 1 and nb_evaluees < nb_attendues:
            # Cursus incomplet : diviser par le nombre réglementaire de périodes officielles
            moyenne_reglementaire = round(somme_moyennes / nb_attendues, 2)
            deliberations[eleve_id] = {
                "moyenne": moyenne_reglementaire,
                "cursus_incomplet": True,
                "statut": "Dossier Incomplet",
                "statut_deliberation": "Dossier Incomplet",
                "suggestion": "Décision réservée au conseil (cursus incomplet)",
                "periodes_evaluees": nb_evaluees,
                "periodes_attendues": nb_attendues,
                "nb_periodes_evaluees": nb_evaluees,
                "nb_periodes_attendues": nb_attendues,
            }
        else:
            diviseur = max(1, nb_attendues if nb_attendues > 1 else nb_evaluees)
            moyenne_reglementaire = round(somme_moyennes / diviseur, 2)
            statut_delib = "Admis" if moyenne_reglementaire >= 10.0 else "Ajourné"
            deliberations[eleve_id] = {
                "moyenne": moyenne_reglementaire,
                "cursus_incomplet": False,
                "statut": "Complet",
                "statut_deliberation": statut_delib,
                "suggestion": "passage" if moyenne_reglementaire >= 10.0 else "redoublement",
                "periodes_evaluees": nb_evaluees,
                "periodes_attendues": nb_attendues,
                "nb_periodes_evaluees": nb_evaluees,
                "nb_periodes_attendues": nb_attendues,
            }

    # 2. Pour les élèves sans bulletin, fallback sur les notes
    sans_bulletin = {i.eleve_id: i for i in inscriptions if i.eleve_id not in deliberations}

    if sans_bulletin:
        notes = Note.query.filter(
            Note.ecole_id == ecole_id,
            Note.annee_id == annee_id,
            Note.eleve_id.in_(sans_bulletin),
            Note.valeur.isnot(None),
            Note.periode.in_(PERIODES_SEMESTRES),
        ).all()

        cours_ids = {n.cours_id for n in notes}
        cours_map = {
            c.id: c
            for c in Cours.query.filter(
                Cours.ecole_id == ecole_id, Cours.id.in_(cours_ids)
            ).all()
        } if cours_ids else {}

        par_eleve = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for note in notes:
            inscription = sans_bulletin[note.eleve_id]
            cours = cours_map.get(note.cours_id)
            if not cours or not cours.classe or cours.classe.annee_scolaire_id != annee_id:
                continue
            if note.inscription_id not in (None, inscription.id):
                continue
            if note.inscription_id is None and cours.classe_id != inscription.classe_id:
                continue
            par_eleve[note.eleve_id][note.periode][cours.id].append(note)

        for eleve_id, par_periode in par_eleve.items():
            semestres = {}
            for periode in PERIODES_SEMESTRES:
                matieres = []
                for cours_id, notes_matiere in par_periode.get(periode, {}).items():
                    moyenne = calculer_moyenne_matiere(notes_matiere)
                    if moyenne is not None:
                        cours = cours_map[cours_id]
                        coef = cours.coefficient if cours.coefficient and cours.coefficient > 0 else 1.0
                        matieres.append((moyenne, coef))
                semestres[periode] = calculer_moyenne_generale_semestre(matieres)

            nb_semestres_eval = sum(1 for p in PERIODES_SEMESTRES if semestres[p] is not None)
            if nb_semestres_eval == len(PERIODES_SEMESTRES):
                annuelle = calculer_moyenne_annuelle(*(semestres[p] for p in PERIODES_SEMESTRES))
                if annuelle is not None:
                    deliberations[eleve_id] = {
                        "moyenne": annuelle,
                        "cursus_incomplet": False,
                        "statut_deliberation": "Complet",
                        "suggestion": "passage" if annuelle >= 10.0 else "redoublement",
                        "periodes_evaluees": nb_semestres_eval,
                        "periodes_attendues": len(PERIODES_SEMESTRES),
                    }
            elif nb_semestres_eval > 0:
                somme_eval = sum(semestres[p] for p in PERIODES_SEMESTRES if semestres[p] is not None)
                moyenne_incomplet = round(somme_eval / len(PERIODES_SEMESTRES), 2)
                deliberations[eleve_id] = {
                    "moyenne": moyenne_incomplet,
                    "cursus_incomplet": True,
                    "statut_deliberation": "Dossier Incomplet",
                    "suggestion": "Décision réservée au conseil (cursus incomplet)",
                    "periodes_evaluees": nb_semestres_eval,
                    "periodes_attendues": len(PERIODES_SEMESTRES),
                    "_fallback_incomplet": True,
                }

    # Un bulletin ou une moyenne par semestre ne prouve pas que toutes les
    # matières obligatoires de chaque période ont été évaluées.
    periodes_a_verifier = periodes_officielles or PERIODES_SEMESTRES
    for inscription in inscriptions:
        resultat = deliberations.get(inscription.eleve_id)
        if resultat is None:
            continue
        completude = calculer_completude_annuelle_inscription(
            ecole_id, annee_id, inscription, periodes_a_verifier
        )
        resultat["missing_subjects_names"] = completude["missing_subjects_names"]
        resultat["missing_subjects_by_period"] = completude["missing_subjects_by_period"]
        if not completude["is_pedagogically_complete"]:
            resultat["cursus_incomplet"] = True
            resultat["statut"] = "Dossier Incomplet"
            resultat["statut_deliberation"] = "Dossier Incomplet"
            resultat["suggestion"] = "Décision réservée au conseil (matières manquantes)"

    return deliberations


def evaluer_deliberation_annuelle(ecole_id, annee_id, eleve_id):
    """Évalue la situation de délibération d'un élève individuel."""
    delibs = get_deliberations_annuelles_eleves(ecole_id, annee_id)
    return delibs.get(eleve_id, {
        "moyenne": None,
        "cursus_incomplet": True,
        "statut_deliberation": "Dossier Incomplet",
        "suggestion": "Décision réservée au conseil (cursus incomplet)",
        "periodes_evaluees": 0,
        "periodes_attendues": 2,
    })


def get_moyennes_annuelles_eleves(ecole_id, annee_id):
    """
    Moyenne des deux semestres selon les mêmes règles que les bulletins.
    Pour les élèves avec bulletins, divise par le nombre réglementaire de périodes officielles.
    Un cursus incomplet en fallback de notes n'est pas émis pour préserver la règle académique.
    """
    delibs = get_deliberations_annuelles_eleves(ecole_id, annee_id)
    return {
        eid: data["moyenne"]
        for eid, data in delibs.items()
        if data.get("moyenne") is not None and not data.get("_fallback_incomplet")
    }


# ---------------------------------------------------------------------------
# 8. Réintégration d'un ancien élève après archivage / droit à l'erreur
# ---------------------------------------------------------------------------

def reinscrire_ancien_eleve(
    eleve_id,
    annee_active_id,
    classe_cible_id,
    ecole_id=None,
    statut="inscrit",
):
    """
    Réintègre un ancien élève (non réinscrit lors du passage ou revenant en cours d'année)
    directement dans une classe de l'année active ou planifiée.
    
    Vérifications :
      - Élève existant et lié à l'école.
      - Année cible existante (statut active ou planifiee).
      - Classe cible existante, ouverte et appartenant à l'année cible.
      - Aucune inscription existante pour cet élève dans cette année.
      - Statut valide parmi STATUTS_INSCRIPTION (défaut: 'inscrit').

    Retourne : (inscription, error_str | None)
    """
    if statut not in STATUTS_INSCRIPTION:
        return None, f"Statut d'inscription invalide : '{statut}'."

    # Résolution et validation élève
    if ecole_id is not None:
        eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    else:
        eleve = Eleve.query.get(eleve_id)
        if eleve:
            ecole_id = eleve.ecole_id

    if not eleve:
        return None, "Élève introuvable."

    # Validation année
    annee_cible = AnneeScolaire.query.filter_by(id=annee_active_id, ecole_id=ecole_id).first()
    if not annee_cible:
        return None, "Année scolaire introuvable pour cet établissement."

    if annee_cible.statut == "archivee":
        return None, "Impossible de réinscrire un élève dans une année archivée."

    # Validation classe cible
    classe_cible = Classe.query.filter_by(id=classe_cible_id, ecole_id=ecole_id).first()
    if not classe_cible:
        return None, "Classe cible introuvable pour cet établissement."
    if classe_cible.annee_scolaire_id != annee_cible.id:
        return None, "La classe cible n'appartient pas à l'année sélectionnée."
    if not classe_est_ouverte(classe_cible):
        return None, "La classe cible est fermée."

    # Vérification absence d'inscription pour l'année
    insc_existante = get_inscription(eleve, annee_cible)
    if insc_existante:
        return None, "L'élève a déjà une inscription pour cette année scolaire."

    sync = (annee_cible.statut == "active" and statut in ("inscrit", "preinscrit"))
    inscription, err = creer_inscription_annuelle(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee_cible.id,
        classe_id=classe_cible.id,
        statut=statut,
        sync_active=sync,
    )
    if err:
        return None, err

    if sync and hasattr(eleve, 'classe_id') and getattr(eleve, 'classe_id') != classe_cible.id:
        eleve.classe_id = classe_cible.id

    db.session.flush()
    return inscription, None


# ---------------------------------------------------------------------------
# 8. Annulation de décision de passage (Chantier B - Droit au remords)
# ---------------------------------------------------------------------------

def annuler_decision_passage(eleve_id, annee_source_id, annee_cible_id, ecole_id):
    """
    Annule une décision de fin d'année (Passage, Redoublement, Sortie, Transfert, Diplôme)
    tant que l'année cible est encore au statut 'planifiee'.

    Actions atomiques :
      1. Vérifier que l'élève et les deux années appartiennent à l'école.
      2. Vérifier que l'année cible est strictement au statut 'planifiee'.
      3. Vérifier que l'année source n'est pas archivée.
      4. Vérifier l'inscription source de l'élève.
      5. Si une inscription cible a été générée :
         - Contrôler l'absence d'opérations associées (paiements, notes, absences).
         - Supprimer l'inscription cible.
      6. Réinitialiser l'inscription source :
         - decision_fin_annee = None
         - motif_sortie = None
         - date_sortie = None
         - statut = 'inscrit'
      7. Valider la transaction atomiquement (commit).

    Retourne : (success: bool, message: str)
    """
    if not ecole_id:
        return False, "Établissement non spécifié."

    # Validation élève
    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        return False, "Élève introuvable pour cet établissement."

    # Validation années scolaires
    annee_source = AnneeScolaire.query.filter_by(id=annee_source_id, ecole_id=ecole_id).first()
    if not annee_source:
        return False, "Année scolaire source introuvable pour cet établissement."

    annee_cible = AnneeScolaire.query.filter_by(id=annee_cible_id, ecole_id=ecole_id).first()
    if not annee_cible:
        return False, "Année scolaire cible introuvable pour cet établissement."

    # Garde stricte : l'année cible doit impérativement être 'planifiee'
    if annee_cible.statut != "planifiee":
        return False, (
            f"Impossible d'annuler la décision : l'année cible '{annee_cible.nom}' est "
            f"au statut '{annee_cible.statut}'. L'annulation n'est autorisée que pour "
            "les années planifiées."
        )

    # Garde : année source archivée
    if annee_source.statut == "archivee":
        return False, (
            "Impossible d'annuler : l'année source est archivée et ne peut plus être modifiée."
        )

    # Inscription source
    insc_source = get_inscription(eleve, annee_source)
    if not insc_source:
        return False, "Aucune inscription trouvée pour cet élève dans l'année source."

    # Inscription cible (si passage ou redoublement déjà exécuté)
    insc_cible = get_inscription(eleve, annee_cible)

    try:
        if insc_cible:
            # Vérifications de sécurité pour ne pas perdre d'opérations
            if hasattr(insc_cible, 'paiements') and insc_cible.paiements:
                return False, (
                    "Impossible d'annuler la décision : des paiements sont déjà enregistrés "
                    "pour cette inscription dans la nouvelle année. Veuillez d'abord régulariser "
                    "ces règlements."
                )
            if hasattr(insc_cible, 'notes') and insc_cible.notes:
                return False, (
                    "Impossible d'annuler la décision : des notes sont déjà associées à "
                    "l'inscription dans l'année cible."
                )
            if hasattr(insc_cible, 'absences') and insc_cible.absences:
                return False, (
                    "Impossible d'annuler la décision : des absences sont déjà associées à "
                    "l'inscription dans l'année cible."
                )

            # Suppression de l'inscription cible
            db.session.delete(insc_cible)

        # Réinitialisation de l'inscription source
        insc_source.decision_fin_annee = None
        insc_source.motif_sortie = None
        insc_source.date_sortie = None
        insc_source.statut = "inscrit"
        insc_source.updated_at = datetime.utcnow()

        db.session.commit()
        return True, "Décision annulée avec succès. L'élève est de nouveau en attente de décision."

    except Exception as exc:
        db.session.rollback()
        return False, f"Erreur lors de l'annulation de la décision : {exc}"
