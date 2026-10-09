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

from datetime import datetime, timedelta
from sqlalchemy import func, or_
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

MODES_PAIEMENT_NIGER = {
    'especes': 'Espèces (Cash)',
    'airtel_money': 'Airtel Money',
    'moov_money': 'Moov Money (Flooz)',
    'al_izza': 'Al Izza Transfert',
    'nita': 'Nita Transfert',
    'amana': 'Amana Transfert',
    'virement': 'Virement bancaire',
    'cheque': 'Chèque'
}
MODES_REGLEMENT_AUTORISES = set(MODES_PAIEMENT_NIGER.keys())


def normaliser_mode_paiement(mode):
    """Valide et normalise le mode de paiement selon les canaux réels du Niger.
    Retourne la clé canonique ou None si mode non reconnu / banni (wave, orange_money, etc.)."""
    if not mode:
        return 'especes'
    m = str(mode).strip().lower()
    if m in MODES_REGLEMENT_AUTORISES:
        return m
    aliases = {
        'espèces': 'especes',
        'especes': 'especes',
        'espèces (cash)': 'especes',
        'especes (cash)': 'especes',
        'cash': 'especes',
        'airtel money': 'airtel_money',
        'moov money': 'moov_money',
        'moov money (flooz)': 'moov_money',
        'flooz': 'moov_money',
        'al izza': 'al_izza',
        'al izza transfert': 'al_izza',
        'al_izza transfert': 'al_izza',
        'nita': 'nita',
        'nita transfert': 'nita',
        'amana': 'amana',
        'amana transfert': 'amana',
        'virement': 'virement',
        'virement bancaire': 'virement',
        'chèque': 'cheque',
        'cheque': 'cheque',
    }
    if m in aliases:
        return aliases[m]
    for k, v in MODES_PAIEMENT_NIGER.items():
        if m == v.lower():
            return k
    return None


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


def _frais_du_inscription(inscription):
    """Retourne les frais exigibles, en conservant la compatibilite historique."""
    base = getattr(inscription, "frais_scolarite", None)
    if base is None:
        base = getattr(inscription, "frais_annuels", None)
    if base is None:
        base = getattr(getattr(inscription, "eleve", None), "frais_annuels", None)
    if base is None:
        base = 150000.0
    remise = getattr(inscription, "remise", 0) or 0
    frais_inscription = getattr(inscription, "frais_inscription", 0) or 0
    return max(0.0, float(base) - float(remise) + float(frais_inscription))


def _synthese_inscription(inscription):
    frais = _frais_du_inscription(inscription)
    total_paye = float(db.session.query(func.coalesce(func.sum(Paiement.montant), 0.0)).filter(
        Paiement.inscription_id == inscription.id,
        Paiement.ecole_id == inscription.ecole_id,
        or_(Paiement.statut.is_(None), Paiement.statut != "annule"),
    ).scalar() or 0.0)
    reste = max(0.0, frais - total_paye)
    pourcentage = 100.0 if frais == 0 else round((total_paye / frais) * 100.0, 1)
    statut = "complet" if reste <= 0 else ("partiel" if total_paye > 0 else "aucun")
    return {
        "inscription": inscription,
        "frais_du": frais,
        "frais_scolarite": float(getattr(inscription, "frais_scolarite", None) or getattr(inscription, "frais_annuels", None) or 0),
        "frais_annuels": frais,
        "remise": float(getattr(inscription, "remise", 0) or 0),
        "frais_inscription": float(getattr(inscription, "frais_inscription", 0) or 0),
        "total_paye": total_paye,
        "reste_a_payer": reste,
        "solde_restant": reste,
        "montant_net": frais,
        "pourcentage_paye": pourcentage,
        "statut_solde": statut,
    }


def obtenir_synthese_financiere_eleve(arg1, arg2, arg3=None):
    """Source canonique du solde d'un élève pour une année scolaire.
    Supporte (eleve_id, annee_scolaire_id) ou (ecole_id, eleve_id, annee_scolaire_id).
    """
    if arg3 is not None:
        ecole_id, eleve_id, annee_scolaire_id = arg1, arg2, arg3
        inscription = Inscription.query.filter_by(
            ecole_id=ecole_id, eleve_id=eleve_id, annee_scolaire_id=annee_scolaire_id
        ).first()
    else:
        eleve_id, annee_scolaire_id = arg1, arg2
        inscription = Inscription.query.filter_by(
            eleve_id=eleve_id, annee_scolaire_id=annee_scolaire_id
        ).first()

    if not inscription:
        return {
            "inscription": None, "frais_du": 0.0, "frais_scolarite": 0.0,
            "frais_annuels": 0.0, "remise": 0.0, "frais_inscription": 0.0,
            "total_paye": 0.0, "reste_a_payer": 0.0, "solde_restant": 0.0,
            "montant_net": 0.0,
            "pourcentage_paye": 0.0, "statut_solde": "aucun",
        }
    return _synthese_inscription(inscription)


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

    return _synthese_inscription(inscription)


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
            or_(Paiement.statut.is_(None), Paiement.statut != "annule"),
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


def enregistrer_paiement(ecole_id, annee, user, eleve_id, montant, mois, annee_civile, mode_paiement="especes", reference=None, idempotency_key=None):
    """Enregistre un nouveau paiement lié à l'inscription de l'année active."""
    mode_canonique = normaliser_mode_paiement(mode_paiement)
    if not mode_canonique:
        return None, "Mode de règlement invalide. Modes autorisés pour le Niger : Airtel Money, Moov Money, Al Izza, Nita, Amana, Espèces, Virement, Chèque."

    idempotency_key = (idempotency_key or "").strip() or None

    # 1. Vérification idempotence explicite par clé unique
    if idempotency_key:
        paiement_existant = Paiement.query.filter_by(
            ecole_id=ecole_id,
            idempotency_key=idempotency_key
        ).first()
        if paiement_existant:
            paiement_existant.deja_traite = True
            return paiement_existant, None

    inscription, error = valider_mutation_paiement(ecole_id, annee, user, eleve_id, montant)
    if error:
        return None, error
    montant_float = float(montant)
    reference = (reference or "").strip() or None

    # 2. Garde-fou anti-rejeu immédiat (< 30s) pour éviter les doubles clics accidentels
    seuil_anti_rejeu = datetime.utcnow() - timedelta(seconds=30)
    paiement_recent = Paiement.query.filter(
        Paiement.ecole_id == ecole_id,
        Paiement.eleve_id == eleve_id,
        Paiement.montant == montant_float,
        Paiement.mode_paiement == mode_canonique,
        Paiement.inscription_id == inscription.id,
        Paiement.date_paiement >= seuil_anti_rejeu,
        or_(Paiement.statut.is_(None), Paiement.statut != "annule")
    ).order_by(Paiement.id.desc()).first()

    if paiement_recent:
        paiement_recent.deja_traite = True
        return paiement_recent, None

    try:
        # PostgreSQL verrouille la ligne; SQLite sérialise l'écriture, ce qui
        # garde le recalcul du solde dans la même unité transactionnelle.
        with db.session.begin_nested():
            inscription = Inscription.query.filter_by(
                id=inscription.id, ecole_id=ecole_id, annee_scolaire_id=annee.id
            ).with_for_update().first()
            if not inscription:
                return None, "Inscription introuvable dans cette année scolaire."
            finances = get_finances_inscription(inscription)
            if montant_float > finances["reste_a_payer"] + 0.01:
                return None, f"Le montant ({montant_float:,.0f} FCFA) dépasse le reste à payer ({finances['reste_a_payer']:,.0f} FCFA)."
            if reference and Paiement.query.filter_by(ecole_id=ecole_id, reference=reference).with_for_update().first():
                return None, "Cette référence de paiement existe déjà."
            paiement = Paiement(
                ecole_id=ecole_id, eleve_id=eleve_id, inscription_id=inscription.id,
                montant=montant_float, mois=mois, annee=int(annee_civile),
                mode_paiement=mode_canonique, reference=reference,
                statut="payé", date_paiement=datetime.utcnow(),
                idempotency_key=idempotency_key,
            )
            db.session.add(paiement)
            paiement.inscription_confirmee = False
            if inscription.statut == "preinscrit":
                inscription.statut = "inscrit"
                paiement.inscription_confirmee = True
                eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
                if eleve and inscription.classe_id and getattr(annee, "statut", None) == "active":
                    eleve.classe_id = inscription.classe_id
            db.session.flush()
    except Exception as exc:
        db.session.rollback()
        if idempotency_key and ("idempotency" in str(exc).lower() or "unique" in str(exc).lower()):
            existant = Paiement.query.filter_by(ecole_id=ecole_id, idempotency_key=idempotency_key).first()
            if existant:
                existant.deja_traite = True
                return existant, None
        if "reference" in str(exc).lower() and reference:
            return None, "Cette référence de paiement existe déjà."
        raise

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
    inscription.frais_scolarite = montant_float
    inscription.updated_at = datetime.utcnow()
    db.session.commit()
    return True, None


def synchroniser_frais_et_remises_inscription(inscription, nouveau_frais=None, nouvelle_remise=None):
    """
    Synchronise instantanément les frais et la remise sur une inscription :
    1. Met à jour frais_scolarite et remise.
    2. Recalcule le net dû : frais_scolarite - remise + frais_inscription.
    """
    if not inscription:
        return None

    if nouveau_frais is not None:
        try:
            val_frais = float(nouveau_frais)
            inscription.frais_scolarite = val_frais
            inscription.frais_annuels = val_frais
            if inscription.eleve:
                inscription.eleve.frais_annuels = val_frais
        except (ValueError, TypeError):
            pass

    if nouvelle_remise is not None:
        try:
            val_remise = float(nouvelle_remise)
            inscription.remise = val_remise
        except (ValueError, TypeError):
            pass

    base = (
        inscription.frais_scolarite
        if inscription.frais_scolarite is not None
        else (inscription.frais_annuels or 150000.0)
    )
    remise = inscription.remise or 0.0
    frais_insc = getattr(inscription, "frais_inscription", 0.0) or 0.0
    frais_nets = max(0.0, float(base) - float(remise) + float(frais_insc))

    if hasattr(inscription, "frais_nets"):
        inscription.frais_nets = frais_nets

    inscription.updated_at = datetime.utcnow()
    db.session.flush()
    return inscription


def calculer_retard_echeancier_inscription(
    inscription,
    annee_scolaire=None,
    date_reference=None,
    seuil_tolerance=1000.0,
):
    """
    Calcule l'état d'impayé / retard selon l'échéancier réel :
    - Montant exigible à date = prorata des mois écoulés de l'année scolaire.
    - retard = montant_cumule_exigible_a_date - total_versements_valides.
    - Si retard > seuil_tolerance : élève en retard, relance active.
    - La présence de micro-versements (ex: 100 FCFA) n'annule PAS la relance
      tant que le montant exigible à date n'est pas couvert.
    """
    if not inscription:
        return {
            "frais_nets": 0.0,
            "total_paye": 0.0,
            "montant_exigible_a_date": 0.0,
            "retard": 0.0,
            "est_en_retard": False,
            "mois_ecoules": 0,
            "total_mois": 0,
            "mois_couverts": 0,
            "seuil_tolerance": float(seuil_tolerance),
        }

    annee = annee_scolaire or inscription.annee_scolaire
    frais_nets = _frais_du_inscription(inscription)

    # Récupérer tous les paiements valides
    total_paye = float(
        sum(
            float(p.montant or 0)
            for p in inscription.paiements
            if (getattr(p, "statut", None) or "payé") not in ("rejete", "annule")
        )
    )

    # Calcul de la timeline
    mois_scolaires_liste = get_mois_scolaires(annee)
    total_mois = len(mois_scolaires_liste) or 9
    maintenant = date_reference or datetime.now()

    month_to_num = {
        "Janvier": 1, "Février": 2, "Mars": 3, "Avril": 4,
        "Mai": 5, "Juin": 6, "Juillet": 7, "Août": 8,
        "Septembre": 9, "Octobre": 10, "Novembre": 11, "Décembre": 12,
    }

    # Calcul des mois écoulés (exigibles)
    mois_ecoules = 0
    if annee and annee.date_debut and annee.date_fin:
        date_ref = maintenant.date() if isinstance(maintenant, datetime) else maintenant
        if date_ref >= annee.date_fin:
            mois_ecoules = total_mois
        elif date_ref < annee.date_debut:
            mois_ecoules = 0
        else:
            for m_name in mois_scolaires_liste:
                m_num = month_to_num.get(m_name, 1)
                y = annee.date_debut.year if m_num >= 8 else annee.date_fin.year
                if (y, m_num) <= (date_ref.year, date_ref.month):
                    mois_ecoules += 1
    else:
        mois_ecoules = 1

    mois_ecoules = max(1, min(mois_ecoules, total_mois))
    frais_mensuel = frais_nets / total_mois if total_mois > 0 else 0.0
    montant_exigible_a_date = min(frais_nets, round(frais_mensuel * mois_ecoules, 2))

    retard = max(0.0, round(montant_exigible_a_date - total_paye, 2))
    est_en_retard = retard > float(seuil_tolerance)
    mois_couverts = int(total_paye // frais_mensuel) if frais_mensuel > 0 else total_mois

    return {
        "frais_nets": frais_nets,
        "total_paye": total_paye,
        "montant_exigible_a_date": montant_exigible_a_date,
        "retard": retard,
        "est_en_retard": est_en_retard,
        "mois_ecoules": mois_ecoules,
        "nb_mois_ecoules": mois_ecoules,
        "total_mois": total_mois,
        "mois_couverts": mois_couverts,
        "seuil_tolerance": float(seuil_tolerance),
    }

