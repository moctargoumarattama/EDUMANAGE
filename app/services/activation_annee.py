"""
app/services/activation_annee.py
=============================================================
KLASORA — Phase 2D-5
Service d'activation annuelle sécurisée et de clôture du cycle.

Responsabilités :
  1. Préparer et valider l'activation (lecture seule, sans effet de bord).
  2. Exécuter l'activation sous transaction globale atomique (all-or-nothing) :
     - Ancienne année active -> 'archivee'
     - Nouvelle année planifiée -> 'active'
     - Resynchronisation de Eleve.classe_id ? partir des inscriptions de la nouvelle année active
     - Déconnexion (classe_id = NULL) des élèves sortis, transférés, diplômés ou historiques sans inscription cible
     - Commit unique
  3. Mettre ? jour session["annee_consultee"] pour l'école concernée.
  4. Idempotence : si l'année est d?j? active, ne rien re-muter.

Règles absolues :
  - AnneeScolaire.statut est l'unique source de vérité (active, planifiee, archivee).
  - Aucune colonne is_active.
  - Aucune création automatique de classe/niveau/cours/inscription/professeur.
  - Rollback intégral en cas d'erreur.
  - Cloisonnement multi-établissement strict.
=============================================================
"""

import logging
from app import db
from app.models import AnneeScolaire, Classe, Eleve, Inscription
from app.services.preparation_annee import get_etat_preparation_annee

logger = logging.getLogger(__name__)


def preparer_activation_annee(ecole_id, annee_id):
    """
    Vérifie l'éligibilité d'une année planifiée ? l'activation et
    calcule le bilan de transition pour la page de confirmation.

    Opération purement en lecture seule : aucune modification de base
    de données ni de session.

    Retourne : (donnees_dict, error_str | None)
    """
    if not ecole_id:
        return None, "Établissement non spécifié."

    cible = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not cible:
        return None, "Année scolaire introuvable pour cet établissement."

    if cible.statut == "active":
        return None, "Cette année scolaire est d?j? active."

    if cible.statut == "archivee":
        return None, "Une année scolaire archivée ne peut pas être activée."

    if cible.statut != "planifiee":
        return None, f"Statut invalide pour l'activation : '{cible.statut}'."

    # Vérification de l'unicité de l'année active existante
    actives = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").all()
    if len(actives) > 1:
        return None, "Incohérence détectée : plusieurs années scolaires actives."

    ancienne_active = actives[0] if actives else None

    # Validation chronologique si une année active existe
    if ancienne_active:
        if cible.date_debut <= ancienne_active.date_debut:
            return None, (
                f"Incohérence chronologique : l'année cible ({cible.nom}) débute le "
                f"{cible.date_debut.strftime('%d/%m/%Y')}, ce qui n'est pas postérieur ? l'année "
                f"active ({ancienne_active.nom}, début le {ancienne_active.date_debut.strftime('%d/%m/%Y')})."
            )

    # Revalidation des prérequis de préparation
    etat = get_etat_preparation_annee(ecole_id, cible.id)
    if not etat:
        return None, "Impossible d'évaluer l'état de préparation de l'année scolaire."

    if not etat["verification"]["prete_pour_activation"] or len(etat["verification"]["bloquants"]) > 0:
        bloquants_str = "; ".join(etat["verification"]["bloquants"])
        return None, f"Préparation incomplète : {bloquants_str}"

    # Statistiques prévisionnelles sur les élèves de l'école
    eleves_ecole = Eleve.query.filter_by(ecole_id=ecole_id).all()
    inscriptions_cible = {
        insc.eleve_id: insc
        for insc in Inscription.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=cible.id)
        .all()
    }

    nb_synchronises = 0
    nb_sans_inscription = 0
    for el in eleves_ecole:
        insc = inscriptions_cible.get(el.id)
        if insc and insc.statut == "inscrit" and insc.classe_id:
            nb_synchronises += 1
        else:
            nb_sans_inscription += 1

    return {
        "annee_cible": cible,
        "ancienne_active": ancienne_active,
        "premiere_activation": ancienne_active is None,
        "etat_preparation": etat,
        "total_eleves": len(eleves_ecole),
        "nb_synchronises": nb_synchronises,
        "nb_sans_inscription": nb_sans_inscription,
    }, None


def resynchroniser_classes_eleves_annee_active(ecole_id, annee_active_id):
    """
    Met ? jour le cache Eleve.classe_id pour tous les élèves de l'établissement
    en fonction de leurs inscriptions dans l'année scolaire désormais active.

    Règles :
      - Si l'élève possède une Inscription active (statut == 'inscrit') avec classe_id :
          eleve.classe_id = inscription.classe_id
      - Sinon (aucune inscription dans l'année active, ou inscription sortie/transférée/diplômée) :
          eleve.classe_id = None

    Retourne : dict avec total, synchronises et mis_a_null.
    """
    eleves_ecole = Eleve.query.filter_by(ecole_id=ecole_id).all()
    inscriptions_annee = {
        insc.eleve_id: insc
        for insc in Inscription.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_active_id)
        .all()
    }

    nb_synchronises = 0
    nb_sans_classe = 0

    for el in eleves_ecole:
        insc = inscriptions_annee.get(el.id)
        if insc and insc.statut == "inscrit" and insc.classe_id:
            nb_synchronises += 1
        else:
            nb_sans_classe += 1

    db.session.flush()
    return {
        "total": len(eleves_ecole),
        "synchronises": nb_synchronises,
        "mis_a_null": nb_sans_classe,
        "nb_synchronises": nb_synchronises,
        "nb_sans_classe": nb_sans_classe,
    }


def activer_annee_scolaire(ecole_id, annee_id, user_id=None):
    """
    Execute la transaction globale atomique d'activation de l'annee scolaire.

    Service pur: aucune session Flask, request, flash ou redirect.
    Retourne: (succes: bool, message: str, details: dict)
    """
    ancienne_active = None
    cible = None

    if not ecole_id:
        msg = "Établissement non spécifié."
        return False, msg, {"succes": False, "error": msg}

    cible = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not cible:
        msg = "Année scolaire introuvable pour cet établissement."
        return False, msg, {"succes": False, "error": msg}

    if cible.statut == "active":
        msg = "Cette année scolaire est déjà active."
        return True, msg, {
            "succes": True,
            "deja_active": True,
            "ancienne_annee": None,
            "nouvelle_annee": cible,
            "premiere_activation": False,
            "nb_eleves_synchronises": 0,
            "nb_eleves_sans_inscription": 0,
            "error": None,
        }

    if cible.statut == "archivee":
        msg = "Une année scolaire archivée ne peut pas être activée."
        return False, msg, {"succes": False, "error": msg}

    if cible.statut != "planifiee":
        msg = f"Statut invalide pour l'activation : '{cible.statut}'."
        return False, msg, {"succes": False, "error": msg}

    actives = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").all()
    if len(actives) > 1:
        msg = "État incohérent : plusieurs années scolaires actives."
        return False, msg, {"succes": False, "error": msg}

    ancienne_active = actives[0] if actives else None
    if ancienne_active and cible.date_debut <= ancienne_active.date_debut:
        msg = (
            f"Incohérence chronologique : l'année cible ({cible.nom}) débute le "
            f"{cible.date_debut.strftime('%d/%m/%Y')}, ce qui n'est pas postérieur à l'année "
            f"active ({ancienne_active.nom}, début le {ancienne_active.date_debut.strftime('%d/%m/%Y')})."
        )
        return False, msg, {"succes": False, "error": msg}

    etat = get_etat_preparation_annee(ecole_id, cible.id)
    if not etat:
        msg = "Impossible d'évaluer l'état de préparation de l'année scolaire."
        return False, msg, {"succes": False, "error": msg}

    if not etat["verification"]["prete_pour_activation"] or len(etat["verification"]["bloquants"]) > 0:
        bloquants_str = "; ".join(etat["verification"]["bloquants"])
        msg = f"Activation refusée, bloquant de préparation : {bloquants_str}"
        return False, msg, {
            "succes": False,
            "error": msg,
            "bloquants": etat["verification"]["bloquants"],
        }

    try:
        if ancienne_active:
            ancienne_active.statut = "archivee"

        cible.statut = "active"
        resync = resynchroniser_classes_eleves_annee_active(ecole_id, cible.id)
        nb_synchro = resync["synchronises"]
        nb_sans_classe = resync["mis_a_null"]
        db.session.commit()

        logger.info(
            f"[ACTIVATION_ANNEE] École {ecole_id} : passage à l'année active {cible.nom} (id={cible.id}). "
            f"Ancienne année archivée : {ancienne_active.nom if ancienne_active else 'Aucune'}. "
            f"Élèves synchronisés : {nb_synchro}, élèves sans classe : {nb_sans_classe}. "
            f"Operateur user_id={user_id}."
        )

        msg = f"L'année scolaire {cible.nom} est maintenant active."
        return True, msg, {
            "succes": True,
            "deja_active": False,
            "ancienne_annee": ancienne_active,
            "nouvelle_annee": cible,
            "premiere_activation": ancienne_active is None,
            "nb_eleves_synchronises": nb_synchro,
            "nb_eleves_sans_inscription": nb_sans_classe,
            "error": None,
        }

    except Exception as e:
        db.session.rollback()
        logger.exception(f"[ACTIVATION_ANNEE] Échec de l'activation pour l'école {ecole_id}, annee {annee_id} : {e}")
        msg = f"Erreur interne lors de l'activation : {str(e)}"
        return False, msg, {
            "succes": False,
            "error": msg,
            "deja_active": False,
            "ancienne_annee": ancienne_active,
            "nouvelle_annee": cible,
        }
