"""app/services/paiements_annuels.py
===================================
Service centralisé pour la gestion annualisée des paiements et frais scolaires (Phase 3B).

Règles canoniques :
- Inscription = source de vérité scolarité et financière de l'année.
- Paiement -> Inscription -> Classe -> AnneeScolaire.
- Eleve.classe_id n'est jamais utilisé pour déduire l'année scolaire d'un paiement ou d'une classe.
- Année ACTIVE : encaissement autorisé, lié obligatoirement à Inscription.
- Année PLANIFIÉE : préparation/modification des frais autorisée, encaissement interdit.
- Année ARCHIVÉE : lecture seule stricte (aucun ajout, modification, suppression).
- Règle 2C-5D : consommation exclusive de get_annee_consultee(ecole_id).
"""

from datetime import datetime
from sqlalchemy.orm import selectinload, joinedload
from app import db
from app.models import (
    AnneeScolaire,
    Classe,
    Eleve,
    Inscription,
    Paiement,
)

MOIS_SCOLAIRES_BASE = [
    "Octobre", "Novembre", "Décembre", "Janvier",
    "Février", "Mars", "Avril", "Mai", "Juin"
]

def get_mois_scolaires(annee_scolaire=None):
    """
    Retourne la liste ordonnée des mois de l'année scolaire.
    Ajoute Juillet si l'année scolaire l'autorise.
    """
    mois = list(MOIS_SCOLAIRES_BASE)
    if annee_scolaire and getattr(annee_scolaire, 'facturer_juillet', False):
        mois.append("Juillet")
    return mois

def get_inscriptions_paiements(ecole_id, annee, user=None):
    """Retourne les inscriptions actives/terminées pour l'année et l'école, filtrées par utilisateur."""
    if not ecole_id or not annee:
        return []

    query = (
        Inscription.query.options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
            selectinload(Inscription.paiements),
        )
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
        )
    )

    role = getattr(user, "role", None)
    if role == "parent":
        enfants_ids = [e.id for e in getattr(user, "enfants", []) if e.ecole_id == ecole_id]
        if not enfants_ids:
            return []
        query = query.filter(Inscription.eleve_id.in_(enfants_ids))
    elif role == "professeur":
        # Un professeur ne gère pas les paiements
        return []

    return query.order_by(Inscription.classe_id, Inscription.eleve_id).all()


def get_finances_inscription(inscription):
    """Calcule le bilan financier strict pour une inscription annuelle donnée."""
    if not inscription:
        return {
            "frais_annuels": 0.0,
            "total_paye": 0.0,
            "reste_a_payer": 0.0,
            "pourcentage_paye": 0.0,
            "statut_solde": "aucun",
        }

    # Tarif de l'inscription : inscription.frais_annuels avec fallback sur eleve.frais_annuels
    frais = inscription.frais_annuels
    if frais is None:
        eleve = getattr(inscription, "eleve", None)
        frais = getattr(eleve, "frais_annuels", None) if eleve else None
        if frais is None:
            frais = 150000.0
    frais = float(frais)

    paiements = getattr(inscription, "paiements", [])
    total_paye = float(sum(p.montant for p in paiements if p.montant and (getattr(p, 'statut', None) or 'payé') != 'annule'))
    reste = max(0.0, frais - total_paye)
    if frais == 0:
        reste = 0.0
        pourcentage = 100.0
        statut_solde = "complet"
    else:
        pourcentage = round((total_paye / frais) * 100, 1) if frais > 0 else 0.0
        if reste <= 0:
            statut_solde = "complet"
        elif total_paye > 0:
            statut_solde = "partiel"
        else:
            statut_solde = "aucun"

    return {
        "frais_annuels": frais,
        "total_paye": total_paye,
        "reste_a_payer": reste,
        "pourcentage_paye": pourcentage,
        "statut_solde": statut_solde,
    }


def get_paiements_annee(ecole_id, annee, user=None, classe_id=None, eleve_id=None):
    """Retourne la liste ordonnée des paiements pour l'année scolaire consultée."""
    if not ecole_id or not annee:
        return []

    inscriptions = get_inscriptions_paiements(ecole_id, annee, user)
    if not inscriptions:
        return []

    if classe_id:
        inscriptions = [ins for ins in inscriptions if ins.classe_id == classe_id]
    if eleve_id:
        inscriptions = [ins for ins in inscriptions if ins.eleve_id == eleve_id]

    inscription_ids = [ins.id for ins in inscriptions]
    if not inscription_ids:
        return []

    paiements = (
        Paiement.query.options(
            joinedload(Paiement.inscription).joinedload(Inscription.classe),
            joinedload(Paiement.inscription).joinedload(Inscription.annee_scolaire),
            joinedload(Paiement.eleve),
        )
        .filter(
            Paiement.ecole_id == ecole_id,
            Paiement.inscription_id.in_(inscription_ids),
        )
        .order_by(Paiement.date_paiement.desc(), Paiement.id.desc())
        .all()
    )

    return paiements


def valider_mutation_paiement(ecole_id, annee, user, eleve_id, montant):
    """Vérifie si un enregistrement de paiement est autorisé dans le contexte annuel."""
    if not ecole_id or not annee:
        return None, "Contexte scolaire introuvable."

    role = getattr(user, "role", None)
    if role not in ("admin", "super_admin"):
        return None, "Seul un administrateur est autorisé à enregistrer des paiements."

    if getattr(user, "ecole_id", None) != ecole_id and role != "super_admin":
        return None, "Action non autorisée pour cet établissement."

    statut_annee = getattr(annee, "statut", None)
    if statut_annee == "archivee":
        return None, "L'année scolaire est archivée : les paiements sont en lecture seule stricte."

    if statut_annee == "planifiee":
        return None, "Les paiements pourront être enregistrés lorsque cette année sera active."

    if statut_annee != "active":
        return None, "Seule l'année active autorise l'encaissement de paiements."

    try:
        montant_float = float(montant)
        if montant_float <= 0:
            return None, "Le montant du paiement doit être supérieur à zéro."
    except (TypeError, ValueError):
        return None, "Montant de paiement invalide."

    # Vérifier l'élève
    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        return None, "Élève introuvable dans cet établissement."

    # Inscription de l'élève dans l'année active
    inscription = Inscription.query.filter_by(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
    ).first()

    if not inscription:
        return None, "L'élève n'est pas inscrit dans cette année scolaire (aucune inscription active)."

    finances = get_finances_inscription(inscription)
    if montant_float > finances["reste_a_payer"] + 0.01:
        return None, f"Le montant ({montant_float:,.0f} FCFA) dépasse le reste à payer ({finances['reste_a_payer']:,.0f} FCFA)."

    return inscription, None


def enregistrer_paiement(ecole_id, annee, user, eleve_id, montant, mois, annee_civile, mode_paiement="espèces", reference=None):
    """Enregistre un nouveau paiement lié à l'inscription de l'année active."""
    inscription, error = valider_mutation_paiement(ecole_id, annee, user, eleve_id, montant)
    if error:
        return None, error

    paiement = Paiement(
        ecole_id=ecole_id,
        eleve_id=eleve_id,
        inscription_id=inscription.id,
        montant=float(montant),
        mois=mois,
        annee=int(annee_civile),
        mode_paiement=mode_paiement or "espèces",
        reference=reference or None,
        statut="payé",
        date_paiement=datetime.utcnow(),
    )
    db.session.add(paiement)

    # Conversion automatique du statut préinscrit vers inscrit lors d'un règlement
    paiement.inscription_confirmee = False
    if inscription and inscription.statut == "preinscrit":
        inscription.statut = "inscrit"
        paiement.inscription_confirmee = True
        eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
        if eleve and inscription.classe_id and getattr(annee, "statut", None) == "active":
            eleve.classe_id = inscription.classe_id

    db.session.flush()

    try:
        from flask import current_app
        if hasattr(current_app, "log_correction"):
            current_app.log_correction(
                action="PAIEMENT_CREE",
                description=f"Nouveau paiement enregistré de {montant} FCFA",
                ecole_id=ecole_id,
                cible_type="paiement",
                cible_id=paiement.id,
                ancienne_valeur=None,
                nouvelle_valeur=f"Montant: {montant}, Élève: {eleve_id}, Inscription: {inscription.id}",
                niveau="info"
            )
    except Exception:
        pass

    return paiement, None


def supprimer_paiement_securise(arg1, arg2, arg3, user):
    """Supprime un paiement avec vérification stricte de l'année active et de l'école."""
    if hasattr(arg2, "statut") or isinstance(arg2, AnneeScolaire):
        ecole_id = arg1
        annee = arg2
        paiement_id = arg3
    else:
        paiement_id = arg1
        ecole_id = arg2
        annee = arg3

    if not annee or getattr(annee, "statut", None) == "archivee":
        return False, "Impossible de supprimer un paiement dans une année archivée."

    if getattr(annee, "statut", None) == "planifiee":
        return False, "Opération non autorisée sur une année planifiée."

    role = getattr(user, "role", None)
    if role not in ("admin", "super_admin"):
        return False, "Permissions insuffisantes pour supprimer un paiement."

    paiement = Paiement.query.filter_by(id=paiement_id, ecole_id=ecole_id).first()
    if not paiement:
        return False, "Paiement introuvable."

    if paiement.inscription and paiement.inscription.annee_scolaire_id != annee.id:
        return False, "Ce paiement n'appartient pas à l'année scolaire en cours."

    ancienne_valeur = f"Paiement ID {paiement.id} (Élève: {paiement.eleve_id}, Montant: {paiement.montant}, Inscription: {paiement.inscription_id})"
    db.session.delete(paiement)
    db.session.commit()

    try:
        from flask import current_app
        if hasattr(current_app, "log_correction"):
            current_app.log_correction(
                action="PAIEMENT_SUPPRIME",
                description=f"Suppression du paiement ID {paiement_id}",
                ecole_id=ecole_id,
                cible_type="paiement",
                cible_id=paiement_id,
                ancienne_valeur=ancienne_valeur,
                nouvelle_valeur=None,
                niveau="info"
            )
    except Exception:
        pass

    return True, None


def modifier_frais_inscription(arg1, arg2, arg3, arg4, user):
    """Modifie le tarif spécifique d'une inscription (autorisé en planifiée et active, interdit en archivée)."""
    if hasattr(arg2, "statut") or isinstance(arg2, AnneeScolaire):
        ecole_id = arg1
        annee = arg2
        inscription_id = arg3
        nouveau_montant = arg4
    else:
        inscription_id = arg1
        ecole_id = arg2
        nouveau_montant = arg3
        annee = arg4

    if not annee or getattr(annee, "statut", None) == "archivee":
        return False, "Impossible de modifier les tarifs dans une année archivée."

    role = getattr(user, "role", None)
    if role not in ("admin", "super_admin"):
        return False, "Permissions insuffisantes pour modifier les frais."

    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=ecole_id).first()
    if not inscription:
        return False, "Inscription introuvable."

    try:
        montant_float = float(nouveau_montant)
        if montant_float < 0:
            return False, "Le montant des frais ne peut pas être négatif."
    except (TypeError, ValueError):
        return False, "Montant des frais invalide."

    inscription.frais_annuels = montant_float
    inscription.updated_at = datetime.utcnow()
    db.session.commit()
    return True, None

