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
from app.models import AnneeScolaire, Classe, Eleve, Inscription
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

    classes = (
        Classe.query
        .filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee_cible_id,
            niveau_id=niveau_id,
            statut="ouverte",
        )
        .order_by(Classe.nom.asc(), Classe.id.asc())
        .all()
    )
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
        # Décisions sans cible : déjà traité si statut cohérent
        if inscription_cible_existante:
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
            inscription_cible, error = creer_inscription_annuelle(
                ecole_id=ecole_id,
                eleve_id=eleve.id,
                annee_scolaire_id=annee_cible.id,
                classe_id=classe_cible.id,
                statut="inscrit",
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

        # 2c. Flush groupé — le commit appartient à l'appelant
        db.session.flush()

    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        return None, f"Erreur inattendue lors du passage — rollback effectué : {exc}"

    return {
        "ok": True,
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

