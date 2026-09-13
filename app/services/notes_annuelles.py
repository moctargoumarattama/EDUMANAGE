"""app/services/notes_annuelles.py
===============================
Service centralisé pour la gestion annualisée des notes et évaluations (Phase 3C).

Règles canoniques :
- Inscription = source de vérité scolarité de l'année pour chaque élève.
- Note -> Inscription -> Classe -> AnneeScolaire.
- Note.annee_id == Note.inscription.annee_scolaire_id == Note.cours.classe.annee_scolaire_id.
- Eleve.classe_id n'est jamais utilisé pour déterminer l'historique de scolarité.
- Année ACTIVE : consultation, saisie, modification, suppression autorisées.
- Année PLANIFIÉE : préparation uniquement, saisie des notes strictement interdite.
- Année ARCHIVÉE : lecture seule stricte (consultation, moyennes historiques, exports autorisés ; aucune création/modification/suppression).
- Règle 2C-5D : consommation exclusive de get_annee_consultee(ecole_id) sans mutation de session["annee_consultee"].
"""

import json
from datetime import datetime
from flask import current_app
from sqlalchemy.orm import joinedload, selectinload
from app import db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
    Professeur,
)
from app.notifications import envoyer_email


MESSAGE_ANNEE_PLANIFIEE = "Les notes pourront être saisies lorsque cette année sera active."
MESSAGE_ANNEE_ARCHIVEE = "Cette année est archivée : les notes sont consultables en lecture seule."


def _is_admin_like(user):
    return getattr(user, "role", None) in {"admin", "super_admin"}


def _professeur(user):
    if not user:
        return None
    prof = getattr(user, "professeur_rel", None)
    if not prof and hasattr(user, "id"):
        prof = Professeur.query.filter_by(
            utilisateur_id=user.id,
            ecole_id=getattr(user, "ecole_id", None)
        ).first()
    return prof


def _professeur_cours_ids(user, annee_id=None):
    """Retourne les identifiants de cours enseignés par ce professeur pour l'année donnée."""
    prof = _professeur(user)
    if not prof:
        return set()

    query = Cours.query.filter_by(professeur_id=prof.id, ecole_id=prof.ecole_id)
    if annee_id:
        query = query.join(Classe, Cours.classe_id == Classe.id).filter(
            Classe.annee_scolaire_id == annee_id
        )
    return {c.id for c in query.all()}


def _professeur_classe_ids(user, annee_id=None):
    """Retourne les identifiants de classes dans lesquelles ce professeur enseigne un cours."""
    prof = _professeur(user)
    if not prof:
        return set()

    query = (
        Classe.query.join(Cours, Cours.classe_id == Classe.id)
        .filter(Cours.professeur_id == prof.id, Classe.ecole_id == prof.ecole_id)
    )
    if annee_id:
        query = query.filter(Classe.annee_scolaire_id == annee_id)
    return {c.id for c in query.all()}


def _parent_enfant_ids(user):
    enfants = getattr(user, "enfants", None)
    if not enfants:
        return set()
    if hasattr(enfants, "all"):
        enfants = enfants.all()
    return {enfant.id for enfant in enfants}


def statut_annee_notes(annee):
    """Retourne un message informatif si l'année est en lecture seule ou en préparation."""
    if not annee:
        return "Aucune année scolaire active ou consultée."
    if annee.statut == "planifiee":
        return MESSAGE_ANNEE_PLANIFIEE
    if annee.statut == "archivee":
        return MESSAGE_ANNEE_ARCHIVEE
    return None


def notes_modifiables(annee, user):
    """Détermine si la saisie, modification ou suppression de notes est permise."""
    return bool(
        annee
        and annee.statut == "active"
        and getattr(user, "role", None) in {"admin", "professeur"}
    )


def get_inscription_eleve_annee(ecole_id, eleve_id, annee_id, classe_id=None):
    """Retrouve l'inscription annuelle d'un élève pour une année (et optionnellement classe)."""
    if not all([ecole_id, eleve_id, annee_id]):
        return None
    query = (
        Inscription.query.options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
        )
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.eleve_id == eleve_id,
            Inscription.annee_scolaire_id == annee_id,
        )
    )
    if classe_id:
        query = query.filter(Inscription.classe_id == classe_id)
    return query.first()


def get_inscriptions_notes(ecole_id, annee, user=None, classe_id=None):
    """Retourne les inscriptions pour l'année consultée, filtrées selon les permissions."""
    if not ecole_id or not annee:
        return []

    query = (
        Inscription.query.options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
            selectinload(Inscription.notes),
        )
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
            Eleve.ecole_id == ecole_id,
        )
    )

    role = getattr(user, "role", None)
    if role == "parent":
        ids = _parent_enfant_ids(user)
        query = query.filter(Inscription.eleve_id.in_(ids)) if ids else query.filter(False)
    elif role == "professeur":
        classe_ids = _professeur_classe_ids(user, annee.id)
        query = query.filter(Inscription.classe_id.in_(classe_ids)) if classe_ids else query.filter(False)

    if classe_id:
        query = query.filter(Inscription.classe_id == classe_id)

    inscriptions = query.all()
    inscriptions.sort(
        key=lambda ins: (
            (ins.classe.nom if ins.classe else ""),
            (ins.eleve.nom if ins.eleve else ""),
            (ins.eleve.prenom if ins.eleve else ""),
        )
    )
    return inscriptions


def get_cours_annee(ecole_id, annee, user=None, classe_id=None):
    """Retourne les cours rattachés aux classes de l'année consultée."""
    if not ecole_id or not annee:
        return []

    query = (
        Cours.query.options(joinedload(Cours.classe))
        .join(Classe, Cours.classe_id == Classe.id)
        .filter(
            Cours.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee.id,
        )
    )

    role = getattr(user, "role", None)
    if role == "professeur":
        prof = _professeur(user)
        if not prof:
            return []
        query = query.filter(Cours.professeur_id == prof.id)

    if classe_id:
        query = query.filter(Cours.classe_id == classe_id)

    return query.order_by(Cours.nom).all()


def get_eleves_choices_notes(ecole_id, annee, user=None, classe_id=None):
    """Retourne les choix élèves sous forme de tuples (id, label) pour l'année consultée."""
    inscriptions = get_inscriptions_notes(ecole_id, annee, user=user, classe_id=classe_id)
    seen = set()
    choices = []
    for ins in inscriptions:
        if not ins.eleve or ins.eleve.id in seen:
            continue
        seen.add(ins.eleve.id)
        classe_nom = ins.classe.nom if ins.classe else "Sans classe"
        label = f"{ins.eleve.prenom} {ins.eleve.nom} - {classe_nom}"
        choices.append((ins.eleve.id, label))
    return choices


def get_cours_choices_notes(ecole_id, annee, user=None, classe_id=None):
    """Retourne les choix cours sous forme de tuples (id, label) pour l'année consultée."""
    cours_list = get_cours_annee(ecole_id, annee, user=user, classe_id=classe_id)
    choices = []
    for c in cours_list:
        classe_nom = c.classe.nom if c.classe else "Sans classe"
        label = f"{c.nom} ({classe_nom})"
        choices.append((c.id, label))
    return choices


def get_notes_annee(ecole_id, annee, user=None, classe_id=None, cours_id=None, eleve_id=None):
    """Retourne toutes les notes de l'année consultée avec filtres et jointures optimisées."""
    if not ecole_id or not annee:
        return []

    query = (
        Note.query.options(
            joinedload(Note.eleve),
            joinedload(Note.cours).joinedload(Cours.classe),
            joinedload(Note.inscription).joinedload(Inscription.classe),
        )
        .filter(
            Note.ecole_id == ecole_id,
            Note.annee_id == annee.id,
        )
    )

    role = getattr(user, "role", None)
    if role == "parent":
        ids = _parent_enfant_ids(user)
        query = query.filter(Note.eleve_id.in_(ids)) if ids else query.filter(False)
    elif role == "professeur":
        c_ids = _professeur_cours_ids(user, annee.id)
        query = query.filter(Note.cours_id.in_(c_ids)) if c_ids else query.filter(False)

    if eleve_id:
        query = query.filter(Note.eleve_id == eleve_id)

    if cours_id:
        query = query.filter(Note.cours_id == cours_id)

    if classe_id:
        query = query.join(Cours, Note.cours_id == Cours.id).filter(Cours.classe_id == classe_id)

    return query.order_by(Note.date_evaluation.desc()).all()


def calculer_statistiques_notes(notes_list):
    """Calcule les statistiques globales pour un ensemble de notes."""
    if not notes_list:
        return {
            "moyenne_generale": 0.0,
            "taux_reussite": 0.0,
            "matieres_evaluees": 0,
            "total_coefficients": 0.0,
            "nb_notes": 0,
        }

    total_pondere = sum((n.valeur or 0.0) * (n.coefficient or 1.0) for n in notes_list)
    total_coefficients = sum(n.coefficient or 1.0 for n in notes_list)
    moyenne_generale = (
        round(total_pondere / total_coefficients, 2) if total_coefficients > 0 else 0.0
    )
    notes_reussites = sum(1 for n in notes_list if (n.valeur or 0.0) >= 10.0)
    taux_reussite = (
        round((notes_reussites / len(notes_list)) * 100, 1) if notes_list else 0.0
    )
    matieres_evaluees = len(set(n.cours_id for n in notes_list if n.cours_id))

    return {
        "moyenne_generale": moyenne_generale,
        "taux_reussite": taux_reussite,
        "matieres_evaluees": matieres_evaluees,
        "total_coefficients": total_coefficients,
        "nb_notes": len(notes_list),
    }


def calculer_moyennes_eleve_annee(inscription_id, ecole_id, annee_id=None):
    """Calcule la moyenne pondérée globale et par matière d'une inscription annuelle."""
    if not inscription_id:
        return {"moyenne": 0.0, "total_coefficients": 0.0, "par_matiere": {}}

    query = Note.query.filter_by(inscription_id=inscription_id, ecole_id=ecole_id)
    if annee_id:
        query = query.filter_by(annee_id=annee_id)
    notes = query.all()

    if not notes:
        return {"moyenne": 0.0, "total_coefficients": 0.0, "par_matiere": {}}

    par_matiere = {}
    total_pondere = 0.0
    total_coef = 0.0

    for n in notes:
        c_id = n.cours_id
        if c_id not in par_matiere:
            par_matiere[c_id] = {"pondere": 0.0, "coef": 0.0, "notes": []}
        coef = n.coefficient or 1.0
        par_matiere[c_id]["pondere"] += (n.valeur or 0.0) * coef
        par_matiere[c_id]["coef"] += coef
        par_matiere[c_id]["notes"].append(n)

        total_pondere += (n.valeur or 0.0) * coef
        total_coef += coef

    result_par_matiere = {}
    for c_id, data in par_matiere.items():
        m = round(data["pondere"] / data["coef"], 2) if data["coef"] > 0 else 0.0
        result_par_matiere[c_id] = {
            "moyenne": m,
            "total_coefficients": data["coef"],
            "nb_notes": len(data["notes"]),
        }

    moyenne_globale = round(total_pondere / total_coef, 2) if total_coef > 0 else 0.0

    return {
        "moyenne": moyenne_globale,
        "total_coefficients": total_coef,
        "par_matiere": result_par_matiere,
    }


def valider_mutation_note(ecole_id, annee, user, eleve_id, cours_id, valeur, coefficient=1.0, note_id=None):
    """Valide les contraintes métiers et d'intégrité annuelle pour la création ou mise à jour d'une note.
    
    Retourne:
        (success: bool, error_message: str | None, inscription: Inscription | None, cours: Cours | None, eleve: Eleve | None)
    """
    if not annee:
        return False, "Aucune année scolaire active ou consultée.", None, None, None

    if annee.statut == "archivee":
        return False, MESSAGE_ANNEE_ARCHIVEE, None, None, None

    if annee.statut == "planifiee":
        return False, MESSAGE_ANNEE_PLANIFIEE, None, None, None

    if annee.statut != "active":
        return False, "Les notes ne peuvent être gérées que pour une année scolaire active.", None, None, None

    role = getattr(user, "role", None)
    if role not in {"admin", "super_admin", "professeur"}:
        return False, "Vous n'êtes pas autorisé à gérer les notes.", None, None, None

    try:
        valeur_num = float(valeur)
        if valeur_num < 0 or valeur_num > 20:
            return False, "La note doit être comprise entre 0 et 20.", None, None, None
    except (ValueError, TypeError):
        return False, "Valeur de note invalide.", None, None, None

    try:
        coef_num = float(coefficient)
        if coef_num <= 0:
            return False, "Le coefficient doit être supérieur à 0.", None, None, None
    except (ValueError, TypeError):
        return False, "Coefficient invalide.", None, None, None

    cours = Cours.query.options(joinedload(Cours.classe)).filter_by(id=cours_id, ecole_id=ecole_id).first()
    if not cours:
        return False, "Cours introuvable pour cette école.", None, None, None

    # Si le cours a une classe, vérifier que la classe appartient à l'année consultée
    if cours.classe:
        if cours.classe.annee_scolaire_id != annee.id:
            return False, "La classe de ce cours n'appartient pas à l'année scolaire active.", None, None, None
        if cours.classe.ecole_id != ecole_id:
            return False, "La classe de ce cours n'appartient pas à votre école.", None, None, None

    # Si professeur, vérifier qu'il enseigne bien ce cours
    if role == "professeur":
        prof = _professeur(user)
        if not prof or cours.professeur_id != prof.id:
            return False, "Vous ne pouvez gérer des notes que pour vos propres cours.", None, None, None

    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        return False, "Élève introuvable pour cette école.", None, None, None

    # Source de vérité annuelle : Inscription
    classe_id = cours.classe_id if cours else None
    inscription = get_inscription_eleve_annee(ecole_id, eleve.id, annee.id, classe_id=classe_id)
    if not inscription:
        return (
            False,
            "Cet élève n'est pas inscrit dans la classe de ce cours pour cette année scolaire.",
            None,
            None,
            None,
        )

    return True, None, inscription, cours, eleve


def creer_note(
    ecole_id,
    annee,
    user,
    eleve_id,
    cours_id,
    valeur,
    coefficient=1.0,
    type_evaluation="Devoir",
    periode="Trimestre 1",
    date_evaluation=None,
):
    """Crée et enregistre une nouvelle note liée à l'inscription annuelle."""
    valide, msg, inscription, cours, eleve = valider_mutation_note(
        ecole_id=ecole_id,
        annee=annee,
        user=user,
        eleve_id=eleve_id,
        cours_id=cours_id,
        valeur=valeur,
        coefficient=coefficient,
    )
    if not valide:
        return None, msg

    try:
        nouvelle_note = Note(
            valeur=float(valeur),
            coefficient=float(coefficient),
            type_evaluation=type_evaluation or "Devoir",
            periode=periode or "Trimestre 1",
            date_evaluation=date_evaluation or datetime.utcnow(),
            annee_id=annee.id,
            inscription_id=inscription.id,
            eleve_id=eleve.id,
            cours_id=cours.id,
            ecole_id=ecole_id,
            sync_version=1,
            last_by_admin=(getattr(user, "role", None) in {"admin", "super_admin"}),
        )
        db.session.add(nouvelle_note)
        db.session.commit()

        # Journalisation
        if hasattr(current_app, "log_correction"):
            try:
                current_app.log_correction(
                    action="ajout",
                    description=f"Note ajoutée pour l'élève {eleve.id} en cours {cours.id}",
                    ecole_id=ecole_id,
                    cible_type="note",
                    cible_id=nouvelle_note.id,
                    ancienne_valeur=None,
                    nouvelle_valeur=json.dumps({
                        "valeur": nouvelle_note.valeur,
                        "coefficient": nouvelle_note.coefficient,
                        "type_evaluation": nouvelle_note.type_evaluation,
                        "periode": nouvelle_note.periode,
                        "eleve_id": nouvelle_note.eleve_id,
                        "cours_id": nouvelle_note.cours_id,
                        "inscription_id": nouvelle_note.inscription_id,
                        "annee_id": nouvelle_note.annee_id,
                    }),
                    niveau="info",
                )
            except Exception as log_err:
                current_app.logger.warning(f"Erreur journalisation note: {log_err}")

        # Notification email parent si configuré
        if eleve.email_parent and cours:
            sujet = f"Nouvelle note en {cours.nom}"
            message = f"""Bonjour,

Une nouvelle note a été ajoutée pour {eleve.prenom} {eleve.nom} en {cours.nom}:
- Note: {valeur}/20
- Type: {type_evaluation}
- Coefficient: {coefficient}
- Période: {periode}

Connectez-vous au portail parent pour plus de détails.

Cordialement,
L'équipe pédagogique"""
            try:
                envoyer_email(eleve.email_parent, sujet, message)
            except Exception as email_err:
                current_app.logger.warning(f"Erreur envoi email note: {email_err}")

        return nouvelle_note, None

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur création note: {e}")
        return None, "Erreur interne lors de la création de la note."


def modifier_note(
    ecole_id,
    annee,
    user,
    note_id,
    valeur,
    coefficient=1.0,
    type_evaluation="Devoir",
    periode="Trimestre 1",
    eleve_id=None,
    cours_id=None,
):
    """Modifie une note existante sous réserve de permissions et du statut actif de l'année."""
    if not annee:
        return None, "Aucune année scolaire active ou consultée."

    if annee.statut == "archivee":
        return None, MESSAGE_ANNEE_ARCHIVEE

    if annee.statut == "planifiee":
        return None, MESSAGE_ANNEE_PLANIFIEE

    if annee.statut != "active":
        return None, "Vous ne pouvez modifier une note que pour une année scolaire active."

    note = Note.query.filter_by(id=note_id, ecole_id=ecole_id).first()
    if not note:
        return None, "Note introuvable."

    target_eleve_id = eleve_id or note.eleve_id
    target_cours_id = cours_id or note.cours_id

    valide, msg, inscription, cours, eleve = valider_mutation_note(
        ecole_id=ecole_id,
        annee=annee,
        user=user,
        eleve_id=target_eleve_id,
        cours_id=target_cours_id,
        valeur=valeur,
        coefficient=coefficient,
        note_id=note.id,
    )
    if not valide:
        return None, msg

    try:
        ancienne_valeur = {
            "valeur": note.valeur,
            "coefficient": note.coefficient,
            "type_evaluation": note.type_evaluation,
            "periode": note.periode,
            "eleve_id": note.eleve_id,
            "cours_id": note.cours_id,
            "inscription_id": note.inscription_id,
        }

        note.valeur = float(valeur)
        note.coefficient = float(coefficient)
        note.type_evaluation = type_evaluation or note.type_evaluation
        note.periode = periode or note.periode
        note.eleve_id = eleve.id
        note.cours_id = cours.id
        note.inscription_id = inscription.id
        note.annee_id = annee.id
        note.sync_version = (note.sync_version or 1) + 1
        note.last_by_admin = (getattr(user, "role", None) in {"admin", "super_admin"})
        note.updated_at = datetime.utcnow()

        db.session.commit()

        if hasattr(current_app, "log_correction"):
            try:
                current_app.log_correction(
                    action="modification",
                    description=f"Note {note.id} modifiée pour l'élève {eleve.id}",
                    ecole_id=ecole_id,
                    cible_type="note",
                    cible_id=note.id,
                    ancienne_valeur=json.dumps(ancienne_valeur),
                    nouvelle_valeur=json.dumps({
                        "valeur": note.valeur,
                        "coefficient": note.coefficient,
                        "type_evaluation": note.type_evaluation,
                        "periode": note.periode,
                        "eleve_id": note.eleve_id,
                        "cours_id": note.cours_id,
                        "inscription_id": note.inscription_id,
                    }),
                    niveau="info",
                )
            except Exception as log_err:
                current_app.logger.warning(f"Erreur journalisation modif note: {log_err}")

        return note, None

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur modification note: {e}")
        return None, "Erreur interne lors de la modification de la note."


def supprimer_note(ecole_id, annee, user, note_id):
    """Supprime une note sous réserve que l'année soit active et que l'utilisateur soit autorisé."""
    if not annee:
        return False, "Aucune année scolaire active ou consultée."

    if annee.statut == "archivee":
        return False, MESSAGE_ANNEE_ARCHIVEE

    if annee.statut == "planifiee":
        return False, MESSAGE_ANNEE_PLANIFIEE

    if annee.statut != "active":
        return False, "Vous ne pouvez supprimer une note que pour une année scolaire active."

    note = Note.query.filter_by(id=note_id, ecole_id=ecole_id).first()
    if not note:
        return False, "Note introuvable."

    role = getattr(user, "role", None)
    if role == "professeur":
        prof = _professeur(user)
        if not prof or not note.cours or note.cours.professeur_id != prof.id:
            return False, "Vous ne pouvez supprimer que les notes de vos propres cours."
    elif role not in {"admin", "super_admin"}:
        return False, "Vous n'êtes pas autorisé à supprimer cette note."

    try:
        note_id_val = note.id
        eleve_id_val = note.eleve_id
        cours_id_val = note.cours_id
        note_dict = note.to_dict()

        db.session.delete(note)
        db.session.commit()

        if hasattr(current_app, "log_correction"):
            try:
                current_app.log_correction(
                    action="suppression",
                    description=f"Note {note_id_val} supprimée (élève {eleve_id_val}, cours {cours_id_val})",
                    ecole_id=ecole_id,
                    cible_type="note",
                    cible_id=note_id_val,
                    ancienne_valeur=json.dumps(note_dict),
                    nouvelle_valeur=None,
                    niveau="info",
                )
            except Exception as log_err:
                current_app.logger.warning(f"Erreur journalisation suppression note: {log_err}")

        return True, None

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression note: {e}")
        return False, "Erreur interne lors de la suppression de la note."
