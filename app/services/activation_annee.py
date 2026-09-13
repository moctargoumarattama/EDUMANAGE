"""
app/services/activation_annee.py
=============================================================
KLASORA â€” Phase 2D-5
Service d'activation annuelle sÃ©curisÃ©e et de clÃ´ture du cycle.

ResponsabilitÃ©s :
  1. PrÃ©parer et valider l'activation (lecture seule, sans effet de bord).
  2. ExÃ©cuter l'activation sous transaction globale atomique (all-or-nothing) :
     - Ancienne annÃ©e active -> 'archivee'
     - Nouvelle annÃ©e planifiÃ©e -> 'active'
     - Resynchronisation de Eleve.classe_id Ã  partir des inscriptions de la nouvelle annÃ©e active
     - DÃ©connexion (classe_id = NULL) des Ã©lÃ¨ves sortis, transfÃ©rÃ©s, diplÃ´mÃ©s ou historiques sans inscription cible
     - Commit unique
  3. Mettre Ã  jour session["annee_consultee"] pour l'Ã©cole concernÃ©e.
  4. Idempotence : si l'annÃ©e est dÃ©jÃ  active, ne rien re-muter.

RÃ¨gles absolues :
  - AnneeScolaire.statut est l'unique source de vÃ©ritÃ© (active, planifiee, archivee).
  - Aucune colonne is_active.
  - Aucune crÃ©ation automatique de classe/niveau/cours/inscription/professeur.
  - Rollback intÃ©gral en cas d'erreur.
  - Cloisonnement multi-Ã©tablissement strict.
=============================================================
"""

import logging
from app import db
from app.models import AnneeScolaire, Classe, Eleve, Inscription
from app.services.preparation_annee import get_etat_preparation_annee

logger = logging.getLogger(__name__)


def preparer_activation_annee(ecole_id, annee_id):
    """
    VÃ©rifie l'Ã©ligibilitÃ© d'une annÃ©e planifiÃ©e Ã  l'activation et
    calcule le bilan de transition pour la page de confirmation.

    OpÃ©ration purement en lecture seule : aucune modification de base
    de donnÃ©es ni de session.

    Retourne : (donnees_dict, error_str | None)
    """
    if not ecole_id:
        return None, "Ã‰tablissement non spÃ©cifiÃ©."

    cible = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not cible:
        return None, "AnnÃ©e scolaire introuvable pour cet Ã©tablissement."

    if cible.statut == "active":
        return None, "Cette annÃ©e scolaire est dÃ©jÃ  active."

    if cible.statut == "archivee":
        return None, "Une annÃ©e scolaire archivÃ©e ne peut pas Ãªtre activÃ©e."

    if cible.statut != "planifiee":
        return None, f"Statut invalide pour l'activation : '{cible.statut}'."

    # VÃ©rification de l'unicitÃ© de l'annÃ©e active existante
    actives = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").all()
    if len(actives) > 1:
        return None, "IncohÃ©rence dÃ©tectÃ©e : plusieurs annÃ©es scolaires actives."

    ancienne_active = actives[0] if actives else None

    # Validation chronologique si une annÃ©e active existe
    if ancienne_active:
        if cible.date_debut <= ancienne_active.date_debut:
            return None, (
                f"IncohÃ©rence chronologique : l'annÃ©e cible ({cible.nom}) dÃ©bute le "
                f"{cible.date_debut.strftime('%d/%m/%Y')}, ce qui n'est pas postÃ©rieur Ã  l'annÃ©e "
                f"active ({ancienne_active.nom}, dÃ©but le {ancienne_active.date_debut.strftime('%d/%m/%Y')})."
            )

    # Revalidation des prÃ©requis de prÃ©paration
    etat = get_etat_preparation_annee(ecole_id, cible.id)
    if not etat:
        return None, "Impossible d'Ã©valuer l'Ã©tat de prÃ©paration de l'annÃ©e scolaire."

    if not etat["verification"]["prete_pour_activation"] or len(etat["verification"]["bloquants"]) > 0:
        bloquants_str = "; ".join(etat["verification"]["bloquants"])
        return None, f"PrÃ©paration incomplÃ¨te : {bloquants_str}"

    # Statistiques prÃ©visionnelles sur les Ã©lÃ¨ves de l'Ã©cole
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
    Met Ã  jour le cache Eleve.classe_id pour tous les Ã©lÃ¨ves de l'Ã©tablissement
    en fonction de leurs inscriptions dans l'annÃ©e scolaire dÃ©sormais active.

    RÃ¨gles :
      - Si l'Ã©lÃ¨ve possÃ¨de une Inscription active (statut == 'inscrit') avec classe_id :
          eleve.classe_id = inscription.classe_id
      - Sinon (aucune inscription dans l'annÃ©e active, ou inscription sortie/transfÃ©rÃ©e/diplÃ´mÃ©e) :
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
            el.classe_id = insc.classe_id
            nb_synchronises += 1
        else:
            el.classe_id = None
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
