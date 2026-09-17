"""
Service de gestion annuelle des emplois du temps - KLASORA Phase 3E
Fournit la logique métier d'ancrage annuel, de détection de conflits horaires,
de gestion des droits selon le cycle de vie de l'année scolaire et d'isolation multi-écoles.
"""

from datetime import datetime, time
from app import db
from app.models import (
    EmploiTemps,
    Classe,
    Cours,
    Professeur,
    Ecole,
    Eleve,
    Inscription,
    AnneeScolaire,
)
from app.services.coherence_temporelle import (
    intervalles_se_chevauchent,
    valider_intervalle_heures,
)

MESSAGE_ANNEE_ARCHIVEE = "Cette année est archivée : l'emploi du temps est consultable en lecture seule."
MESSAGE_ANNEE_PLANIFIEE = "Préparation de l'emploi du temps"

JOURS_ORDRE = {
    'Lundi': 1,
    'Mardi': 2,
    'Mercredi': 3,
    'Jeudi': 4,
    'Vendredi': 5,
    'Samedi': 6,
    'Dimanche': 7,
}


def statut_annee_emploi(annee):
    """Retourne le statut de l'année scolaire ou 'inconnue'."""
    if not annee:
        return 'inconnue'
    return annee.statut or 'active'


def peut_modifier_emploi_temps(annee):
    """
    Règle de cycle de vie Phase 3E :
    - active : modification autorisée (gestion courante)
    - planifiee : modification autorisée (préparation de la rentrée)
    - archivee : lecture seule stricte (interdiction d'ajout, modification, suppression)
    """
    if not annee:
        return False
    return annee.statut in ('active', 'planifiee')


def get_creneaux_annee(ecole_id, annee, classe_id=None, professeur_id=None):
    """
    Récupère les créneaux horaires rattachés à l'école et à l'année scolaire spécifiée.
    L'ancrage annuel s'effectue strictement via Classe.annee_scolaire_id.
    """
    if not annee:
        return []

    from sqlalchemy.orm import joinedload
    query = (
        EmploiTemps.query.join(Classe, EmploiTemps.classe_id == Classe.id)
        .options(
            joinedload(EmploiTemps.classe),
            joinedload(EmploiTemps.cours),
            joinedload(EmploiTemps.professeur)
        )
        .filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee.id,
        )
    )

    if classe_id:
        query = query.filter(EmploiTemps.classe_id == classe_id)

    if professeur_id:
        query = query.filter(EmploiTemps.professeur_id == professeur_id)

    creneaux = query.all()

    # Tri standardisé : Jour puis Heure de début
    creneaux.sort(
        key=lambda e: (
            JOURS_ORDRE.get(e.jour, 99),
            e.heure_debut or time.min,
        )
    )
    return creneaux


def detecter_conflits(ecole_id, annee, jour, heure_debut, heure_fin, classe_id, professeur_id, salle=None, exclude_id=None):
    """
    Détecte les conflits horaires dans la même école et la même année scolaire :
    - Règle de chevauchement strict : debut_A < fin_B ET fin_A > debut_B
    - Conflit 1 : même classe + créneaux qui se chevauchent
    - Conflit 2 : même professeur + créneaux qui se chevauchent
    - Conflit 3 : même salle + créneaux qui se chevauchent (si salle renseignée)

    Retourne (has_conflict: bool, error_message: str ou None).
    """
    if not annee:
        return True, "Année scolaire non définie."

    ok_heures, err_heures = valider_intervalle_heures(heure_debut, heure_fin)
    if not ok_heures:
        return True, err_heures

    # Récupérer tous les créneaux du même jour dans la même école et même année scolaire
    creneaux_jour = (
        EmploiTemps.query.join(Classe, EmploiTemps.classe_id == Classe.id)
        .filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee.id,
            EmploiTemps.jour == jour,
        )
        .all()
    )

    salle_clean = salle.strip().lower() if salle and salle.strip() else None

    for c in creneaux_jour:
        if exclude_id and c.id == exclude_id:
            continue

        # Test de chevauchement temporel
        if intervalles_se_chevauchent(heure_debut, heure_fin, c.heure_debut, c.heure_fin):
            c_debut_str = c.heure_debut.strftime('%H:%M') if c.heure_debut else ''
            c_fin_str = c.heure_fin.strftime('%H:%M') if c.heure_fin else ''

            # Conflit classe
            if c.classe_id == classe_id:
                classe_nom = c.classe.nom if c.classe else f"ID {c.classe_id}"
                return True, (
                    f"Conflit de classe : la classe '{classe_nom}' a déjà un cours "
                    f"({c.cours.nom if c.cours else 'Cours'}) de {c_debut_str} à {c_fin_str} le {jour}."
                )

            # Conflit professeur
            if professeur_id and c.professeur_id == professeur_id:
                prof_nom = f"{c.professeur.prenom} {c.professeur.nom}" if c.professeur else f"ID {c.professeur_id}"
                return True, (
                    f"Conflit enseignant : {prof_nom} a déjà un cours avec la classe "
                    f"'{c.classe.nom if c.classe else ''}' de {c_debut_str} à {c_fin_str} le {jour}."
                )

            # Conflit salle
            if salle_clean and c.salle and c.salle.strip().lower() == salle_clean:
                return True, (
                    f"Conflit de salle : la salle '{c.salle.strip()}' est déjà occupée "
                    f"de {c_debut_str} à {c_fin_str} le {jour}."
                )

    return False, None


def valider_et_creer_creneau(ecole_id, annee, classe_id, cours_id, professeur_id, jour, heure_debut, heure_fin, salle=None):
    """
    Valide les contraintes d'intégrité annuelle et crée un nouveau créneau.
    Retourne (creneau, error_message).
    """
    if not peut_modifier_emploi_temps(annee):
        return None, MESSAGE_ANNEE_ARCHIVEE

    # Validation classe
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe introuvable pour votre établissement."
    if classe.annee_scolaire_id != annee.id:
        return None, "La classe n'appartient pas à l'année scolaire consultée."

    # Validation cours
    cours = Cours.query.filter_by(id=cours_id, ecole_id=ecole_id).first()
    if not cours:
        return None, "Cours introuvable pour votre établissement."
    if cours.classe_id != classe.id:
        return None, "Le cours doit obligatoirement appartenir à la classe sélectionnée."

    # Validation professeur : privilégier le professeur du cours si renseigné
    if professeur_id:
        p_check = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
        if not p_check:
            return None, "Enseignant introuvable pour votre établissement."

    prof_final_id = cours.professeur_id or professeur_id
    professeur = Professeur.query.filter_by(id=prof_final_id, ecole_id=ecole_id).first()
    if not professeur:
        return None, "Enseignant introuvable pour votre établissement."

    # Détection des conflits
    has_conflict, conflict_msg = detecter_conflits(
        ecole_id=ecole_id,
        annee=annee,
        jour=jour,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        classe_id=classe.id,
        professeur_id=professeur.id,
        salle=salle,
    )
    if has_conflict:
        return None, conflict_msg

    creneau = EmploiTemps(
        classe_id=classe.id,
        cours_id=cours.id,
        professeur_id=professeur.id,
        jour=jour,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        salle=salle.strip() if salle and salle.strip() else None,
        ecole_id=ecole_id,
    )
    db.session.add(creneau)
    db.session.commit()
    return creneau, None


def valider_et_modifier_creneau(ecole_id, annee, creneau_id, classe_id, cours_id, professeur_id, jour, heure_debut, heure_fin, salle=None):
    """
    Valide les contraintes d'intégrité annuelle et met à jour un créneau existant.
    Retourne (creneau, error_message).
    """
    if not peut_modifier_emploi_temps(annee):
        return None, MESSAGE_ANNEE_ARCHIVEE

    creneau = EmploiTemps.query.filter_by(id=creneau_id, ecole_id=ecole_id).first()
    if not creneau:
        return None, "Créneau horaire introuvable."

    if creneau.classe.annee_scolaire_id != annee.id:
        return None, "Ce créneau appartient à une autre année scolaire."

    # Validation classe
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe introuvable pour votre établissement."
    if classe.annee_scolaire_id != annee.id:
        return None, "La classe n'appartient pas à l'année scolaire consultée."

    # Validation cours
    cours = Cours.query.filter_by(id=cours_id, ecole_id=ecole_id).first()
    if not cours:
        return None, "Cours introuvable pour votre établissement."
    if cours.classe_id != classe.id:
        return None, "Le cours doit obligatoirement appartenir à la classe sélectionnée."

    # Validation professeur : privilégier le professeur du cours si renseigné
    if professeur_id:
        p_check = Professeur.query.filter_by(id=professeur_id, ecole_id=ecole_id).first()
        if not p_check:
            return None, "Enseignant introuvable pour votre établissement."

    prof_final_id = cours.professeur_id or professeur_id
    professeur = Professeur.query.filter_by(id=prof_final_id, ecole_id=ecole_id).first()
    if not professeur:
        return None, "Enseignant introuvable pour votre établissement."

    # Détection des conflits
    has_conflict, conflict_msg = detecter_conflits(
        ecole_id=ecole_id,
        annee=annee,
        jour=jour,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        classe_id=classe.id,
        professeur_id=professeur.id,
        salle=salle,
        exclude_id=creneau.id,
    )
    if has_conflict:
        return None, conflict_msg

    creneau.classe_id = classe.id
    creneau.cours_id = cours.id
    creneau.professeur_id = professeur.id
    creneau.jour = jour
    creneau.heure_debut = heure_debut
    creneau.heure_fin = heure_fin
    creneau.salle = salle.strip() if salle and salle.strip() else None
    db.session.commit()
    return creneau, None


def supprimer_creneau(ecole_id, annee, creneau_id):
    """
    Supprime un créneau horaire après validation de l'année et des permissions.
    Retourne (success: bool, error_message: str ou None).
    """
    if not peut_modifier_emploi_temps(annee):
        return False, MESSAGE_ANNEE_ARCHIVEE

    creneau = EmploiTemps.query.filter_by(id=creneau_id, ecole_id=ecole_id).first()
    if not creneau:
        return False, "Créneau horaire introuvable."

    if creneau.classe.annee_scolaire_id != annee.id:
        return False, "Ce créneau appartient à une autre année scolaire."

    db.session.delete(creneau)
    db.session.commit()
    return True, None


def get_classes_pour_utilisateur(ecole_id, annee, user):
    """
    Retourne les classes consultables selon le rôle de l'utilisateur dans l'année consultée :
    - admin / super_admin : toutes les classes de l'école dans l'année consultée
    - professeur : classes où il enseigne dans l'année consultée
    - parent : classes de ses enfants inscrits dans l'année consultée (via Inscription)
    """
    if not annee:
        return []

    if user.role in ('admin', 'super_admin'):
        return (
            Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
            .order_by(Classe.nom)
            .all()
        )

    if user.role == 'professeur':
        prof = Professeur.query.filter_by(utilisateur_id=user.id).first()
        if not prof:
            return []
        # Classes où le professeur dispense un cours dans l'année consultée
        # ou a un créneau d'emploi du temps
        classes_ids = (
            db.session.query(Cours.classe_id)
            .join(Classe, Cours.classe_id == Classe.id)
            .filter(
                Cours.professeur_id == prof.id,
                Classe.ecole_id == ecole_id,
                Classe.annee_scolaire_id == annee.id,
            )
            .union(
                db.session.query(EmploiTemps.classe_id)
                .join(Classe, EmploiTemps.classe_id == Classe.id)
                .filter(
                    EmploiTemps.professeur_id == prof.id,
                    Classe.ecole_id == ecole_id,
                    Classe.annee_scolaire_id == annee.id,
                )
            )
            .all()
        )
        ids = [cid[0] for cid in classes_ids if cid[0]]
        if not ids:
            return []
        return Classe.query.filter(Classe.id.in_(ids)).order_by(Classe.nom).all()

    if user.role == 'parent':
        # Découplage strict de Eleve.classe_id : source de vérité = Inscription
        enfants = Eleve.query.filter_by(parent_id=user.id, ecole_id=ecole_id).all()
        if not enfants:
            return []
        enfant_ids = [e.id for e in enfants]
        inscriptions = (
            Inscription.query.filter(
                Inscription.eleve_id.in_(enfant_ids),
                Inscription.annee_scolaire_id == annee.id,
                Inscription.statut == 'active',
            )
            .all()
        )
        classes_dict = {}
        for ins in inscriptions:
            if ins.classe and ins.classe.ecole_id == ecole_id:
                classes_dict[ins.classe.id] = ins.classe
        return sorted(list(classes_dict.values()), key=lambda c: c.nom)

    return []


def donnees_impression_classe(ecole_id, annee, classe_id, user=None):
    """
    Prépare les données pour l'impression ou l'export de l'emploi du temps d'une classe :
    - école
    - année scolaire (nom issu de l'entité annuelle)
    - classe
    - créneaux ordonnés
    Vérifie les permissions si un utilisateur est fourni.
    """
    if not annee:
        return None, "Année scolaire non définie."

    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, "Classe introuvable."
    if classe.annee_scolaire_id != annee.id:
        return None, "La classe ne correspond pas à l'année scolaire demandée."

    if user and user.role not in ('admin', 'super_admin'):
        classes_autorisees = get_classes_pour_utilisateur(ecole_id, annee, user)
        if classe.id not in [c.id for c in classes_autorisees]:
            return None, "Accès non autorisé à l'emploi du temps de cette classe."

    creneaux = get_creneaux_annee(ecole_id, annee, classe_id=classe.id)
    ecole = Ecole.query.get(ecole_id)

    return {
        "ecole_nom": ecole.nom if ecole else "Établissement",
        "annee_scolaire_nom": annee.nom,
        "classe_nom": classe.nom,
        "classe_niveau": classe.niveau or "",
        "classe_salle": classe.salle or "",
        "creneaux": [c.to_dict() for c in creneaux],
    }, None
