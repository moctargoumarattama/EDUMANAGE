"""Dispenses de matière accordées par l'administration pour une inscription."""
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Cours, DispenseMatiere, Inscription, Note, Utilisateur
from app.services.notes_annuelles import erreur_verrou_notes, periodes_notes_classe


ANNEE_ENTIERE = '*'


def cours_dispenses(ecole_id, inscription_id, periode):
    """Identifiants des cours dispensés pour cette période, sans effet inter-écoles."""
    if not ecole_id or not inscription_id or not periode:
        return set()
    return {
        row.cours_id for row in DispenseMatiere.query.filter(
            DispenseMatiere.ecole_id == ecole_id,
            DispenseMatiere.inscription_id == inscription_id,
            DispenseMatiere.active.is_(True),
            DispenseMatiere.periode.in_((ANNEE_ENTIERE, periode)),
        ).all()
    }


def est_cours_dispense(ecole_id, inscription_id, cours_id, periode):
    return cours_id in cours_dispenses(ecole_id, inscription_id, periode)


def accorder_dispense(ecole_id, inscription_id, cours_id, periode, reference, utilisateur_id):
    administrateur = Utilisateur.query.filter_by(id=utilisateur_id, ecole_id=ecole_id, role='admin').first()
    if not administrateur:
        return None, "Seul un administrateur de l'établissement peut accorder une dispense."
    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=ecole_id).first()
    if not inscription or not inscription.annee_scolaire or inscription.annee_scolaire.statut != 'active':
        return None, "L'inscription doit appartenir à une année scolaire active."
    if inscription.statut not in {'inscrit', 'actif'}:
        return None, "L'élève doit être scolarisé pour recevoir une dispense."
    cours = Cours.query.filter_by(id=cours_id, ecole_id=ecole_id, classe_id=inscription.classe_id).first()
    if not cours:
        return None, "La matière n'appartient pas à la classe de l'élève."
    if not isinstance(reference, str) or not isinstance(periode, str):
        return None, "Données de dispense invalides."
    reference = reference.strip()
    if not reference or len(reference) > 100:
        return None, "Une référence de justificatif de 1 à 100 caractères est obligatoire."
    periode = (periode or ANNEE_ENTIERE).strip()
    periodes = periodes_notes_classe(inscription.classe, inscription.annee_scolaire)
    if periode != ANNEE_ENTIERE and periode not in periodes:
        return None, "Période invalide pour cette classe."
    cibles = periodes if periode == ANNEE_ENTIERE else [periode]
    for cible in cibles:
        verrou = erreur_verrou_notes(ecole_id, inscription.annee_scolaire_id, cible, inscription)
        if verrou:
            return None, verrou

    conflit = DispenseMatiere.query.filter(
        DispenseMatiere.ecole_id == ecole_id,
        DispenseMatiere.inscription_id == inscription.id,
        DispenseMatiere.cours_id == cours.id,
        DispenseMatiere.active.is_(True),
    ).all()
    if any(d.periode == ANNEE_ENTIERE or periode == ANNEE_ENTIERE or d.periode == periode for d in conflit):
        return None, "Une dispense active couvre déjà cette matière et cette période."

    notes = Note.query.filter(
        Note.ecole_id == ecole_id,
        Note.eleve_id == inscription.eleve_id,
        Note.cours_id == cours.id,
        or_(Note.inscription_id == inscription.id, Note.inscription_id.is_(None)),
        Note.annee_id == inscription.annee_scolaire_id,
    )
    if periode != ANNEE_ENTIERE:
        notes = notes.filter(Note.periode == periode)
    if notes.first():
        return None, "Une note existe déjà : corrigez-la avant d'accorder la dispense."

    dispense = DispenseMatiere.query.filter_by(
        inscription_id=inscription.id, cours_id=cours.id, periode=periode
    ).first()
    if dispense:
        dispense.active = True
        dispense.reference_justificatif = reference
        dispense.cree_par_id = utilisateur_id
        dispense.date_creation = datetime.utcnow()
        dispense.annulee_par_id = None
        dispense.date_annulation = None
    else:
        dispense = DispenseMatiere(
            ecole_id=ecole_id, annee_id=inscription.annee_scolaire_id,
            inscription_id=inscription.id, cours_id=cours.id, periode=periode,
            motif='medicale', reference_justificatif=reference,
            cree_par_id=utilisateur_id, active=True,
        )
        db.session.add(dispense)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, "Cette dispense a déjà été enregistrée."
    return dispense, None


def revoquer_dispense(ecole_id, dispense_id, utilisateur_id):
    if not Utilisateur.query.filter_by(id=utilisateur_id, ecole_id=ecole_id, role='admin').first():
        return None, "Seul un administrateur de l'établissement peut annuler une dispense."
    dispense = DispenseMatiere.query.filter_by(id=dispense_id, ecole_id=ecole_id, active=True).first()
    if not dispense:
        return None, "Dispense introuvable ou déjà annulée."
    inscription = dispense.inscription
    if not inscription or not inscription.annee_scolaire or inscription.annee_scolaire.statut != 'active':
        return None, "Une dispense d'année archivée ne peut pas être modifiée."
    periodes = periodes_notes_classe(inscription.classe, inscription.annee_scolaire)
    cibles = periodes if dispense.periode == ANNEE_ENTIERE else [dispense.periode]
    for cible in cibles:
        verrou = erreur_verrou_notes(ecole_id, inscription.annee_scolaire_id, cible, inscription)
        if verrou:
            return None, verrou
    dispense.active = False
    dispense.annulee_par_id = utilisateur_id
    dispense.date_annulation = datetime.utcnow()
    db.session.commit()
    return dispense, None
