"""
Service de gestion des bulletins scolaires annualisés (Phase 3D).

Règles fondamentales :
- Le bulletin appartient à Inscription (source de vérité).
- L'inscription fournit l'élève, la classe historique, l'année scolaire et l'école.
- Les notes utilisées sont filtrées exclusivement sur Note.inscription_id.
- Eleve.classe_id n'est JAMAIS utilisé comme source historique du bulletin.
- Année active : consultation, génération, aperçu, modification appréciations, PDF.
- Année planifiée : pas de bulletin académique. Message :
  "Les bulletins pourront être générés lorsque cette année sera active."
- Année archivée : consultation, téléchargement PDF, impression en lecture seule stricte.
  Aucune mutation / suppression / recalcul destructif.
- Règle 2C-5D : La consultation n'altère jamais session["annee_consultee"].
"""
from datetime import datetime
from collections import defaultdict
from flask import current_app
from sqlalchemy.orm import joinedload
from app import db
from app.models import (
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Eleve,
    Inscription,
    Note,
    PeriodeBulletin,
)


MESSAGE_ANNEE_PLANIFIEE = "Les bulletins pourront être générés lorsque cette année sera active."
MESSAGE_ANNEE_ARCHIVEE = "Cette année est archivée : les bulletins sont consultables en lecture seule."


def statut_annee_bulletins(annee):
    """Renvoie le message d'avertissement selon le statut de l'année scolaire."""
    if not annee:
        return "Aucune année scolaire active configurée."
    if annee.statut == "planifiee":
        return MESSAGE_ANNEE_PLANIFIEE
    if annee.statut == "archivee":
        return MESSAGE_ANNEE_ARCHIVEE
    return None


def bulletins_modifiables(annee):
    """Indique si les bulletins de l'année peuvent être modifiés ou générés."""
    return bool(annee and annee.statut == "active")


def get_inscriptions_bulletins(ecole_id, annee, user):
    """
    Récupère les inscriptions annuelles éligibles pour l'affichage des bulletins
    selon le rôle de l'utilisateur et l'année consultée.
    """
    if not annee:
        return []

    q = (
        Inscription.query.options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
            joinedload(Inscription.annee_scolaire),
        )
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
        )
    )

    if user.role == 'parent':
        q = q.join(Eleve, Inscription.eleve_id == Eleve.id).filter(Eleve.parent_id == user.id)
    elif user.role == 'professeur':
        professeur = getattr(user, 'professeur_rel', None)
        if professeur:
            # Classes assignées au professeur
            classes_assignees_ids = [c.id for c in professeur.classes_assignees.filter_by(ecole_id=ecole_id).all()]
            # Ou classes où le professeur dispense un cours dans cette école et cette année
            classes_cours_ids = [
                c.id for c in Classe.query.join(Cours)
                .filter(
                    Cours.professeur_id == professeur.id,
                    Cours.ecole_id == ecole_id,
                    Classe.annee_scolaire_id == annee.id
                ).all()
            ]
            allowed_class_ids = set(classes_assignees_ids + classes_cours_ids)
            if allowed_class_ids:
                q = q.filter(Inscription.classe_id.in_(allowed_class_ids))
            else:
                return []
        else:
            return []
    elif user.role != 'admin':
        return []

    return q.order_by(Inscription.classe_id.asc()).all()


def calculer_bulletin_data(ecole_id, annee, inscription, periode=None):
    """
    Calcule toutes les données académiques pour le bulletin d'une Inscription.
    Les notes proviennent exclusivement de Note.inscription_id == inscription.id.
    """
    if not inscription or inscription.ecole_id != ecole_id:
        return None, "Inscription introuvable ou non autorisée."

    # Sélection des notes de cette inscription uniquement
    q = Note.query.options(joinedload(Note.cours)).filter(
        Note.inscription_id == inscription.id,
        Note.ecole_id == ecole_id
    )
    if periode:
        q = q.filter(Note.periode == periode)

    notes = q.order_by(Note.cours_id, Note.date_evaluation.desc()).all()

    # Regroupement des notes par cours / matière
    notes_par_cours = defaultdict(list)
    for n in notes:
        cours_nom = n.cours.nom if n.cours else (n.matiere or "Non renseigné")
        notes_par_cours[cours_nom].append(n)

    # Calcul des moyennes par cours pondérées par les coefficients des notes
    moyennes_par_cours = {}
    coefficients_par_cours = {}

    for cours_nom, c_notes in notes_par_cours.items():
        total_pondere = sum((n.valeur or 0.0) * (n.coefficient or 1.0) for n in c_notes)
        total_coefs = sum((n.coefficient or 1.0) for n in c_notes)
        moyenne_c = round(total_pondere / total_coefs, 2) if total_coefs > 0 else 0.0
        moyennes_par_cours[cours_nom] = moyenne_c
        # Coefficient matière : soit depuis le cours, soit moyen des notes
        coef_cours = c_notes[0].cours.coefficient if (c_notes and c_notes[0].cours and c_notes[0].cours.coefficient) else 1.0
        coefficients_par_cours[cours_nom] = coef_cours

    # Moyenne générale pondérée par les coefficients des matières (ou notes)
    if moyennes_par_cours:
        total_pondere_gen = sum(moyennes_par_cours[c] * coefficients_par_cours.get(c, 1.0) for c in moyennes_par_cours)
        total_coef_gen = sum(coefficients_par_cours.get(c, 1.0) for c in moyennes_par_cours)
        moyenne_generale = round(total_pondere_gen / total_coef_gen, 2) if total_coef_gen > 0 else 0.0
    else:
        moyenne_generale = 0.0

    # Mention / appréciation
    if notes:
        if moyenne_generale >= 16:
            appreciation = "Excellent"
            appreciation_code = "excellent"
            badge_class = "badge-mention-excellent bg-success text-white"
        elif moyenne_generale >= 14:
            appreciation = "Très bien"
            appreciation_code = "tres-bien"
            badge_class = "badge-mention-tres-bien bg-info text-dark"
        elif moyenne_generale >= 12:
            appreciation = "Bien"
            appreciation_code = "bien"
            badge_class = "badge-mention-bien bg-primary text-white"
        elif moyenne_generale >= 10:
            appreciation = "Assez bien"
            appreciation_code = "assez-bien"
            badge_class = "badge-mention-assez-bien bg-warning text-dark"
        else:
            appreciation = "Insuffisant"
            appreciation_code = "insuffisant"
            badge_class = "badge-mention-insuffisant bg-danger text-white"
    else:
        appreciation = "Non évalué"
        appreciation_code = "non-evalue"
        badge_class = "badge-mention-non-evalue bg-secondary text-white"

    # Calcul du rang au sein de la classe annuelle
    rang, rang_total = _calculer_rang_classe(
        ecole_id,
        inscription.classe_id,
        inscription.annee_scolaire_id,
        inscription.id,
        periode=periode
    )

    data = {
        'inscription': inscription,
        'eleve': inscription.eleve,
        'classe': inscription.classe,
        'annee_scolaire': inscription.annee_scolaire,
        'periode': periode or "Trimestre 1",
        'notes': notes,
        'notes_par_cours': dict(notes_par_cours),
        'moyennes_par_cours': moyennes_par_cours,
        'coefficients_par_cours': coefficients_par_cours,
        'moyenne_generale': moyenne_generale,
        'notes_count': len(notes),
        'rang': rang,
        'rang_total': rang_total,
        'appreciation': appreciation,
        'appreciation_code': appreciation_code,
        'badge_class': badge_class,
    }
    return data, None


def _calculer_rang_classe(ecole_id, classe_id, annee_scolaire_id, target_inscription_id, periode=None):
    """
    Calcule le rang d'une inscription parmi tous les élèves inscrits dans la même
    classe et même année scolaire (population Inscription stricte).
    """
    inscriptions_classe = Inscription.query.filter_by(
        ecole_id=ecole_id,
        classe_id=classe_id,
        annee_scolaire_id=annee_scolaire_id
    ).all()

    inscr_ids = [ins.id for ins in inscriptions_classe]
    if not inscr_ids:
        return None, 0

    # Récupération de toutes les notes pour ces inscriptions
    q = Note.query.filter(
        Note.inscription_id.in_(inscr_ids),
        Note.ecole_id == ecole_id
    )
    if periode:
        q = q.filter(Note.periode == periode)
    notes_toutes = q.all()

    notes_par_insc = defaultdict(list)
    for n in notes_toutes:
        notes_par_insc[n.inscription_id].append(n)

    # Calcul des moyennes pour chaque élève inscrit
    scores = []
    for ins_id in inscr_ids:
        ins_notes = notes_par_insc.get(ins_id, [])
        if ins_notes:
            tot_p = sum((n.valeur or 0.0) * (n.coefficient or 1.0) for n in ins_notes)
            tot_c = sum((n.coefficient or 1.0) for n in ins_notes)
            moy = round(tot_p / tot_c, 2) if tot_c > 0 else 0.0
            scores.append((ins_id, moy, len(ins_notes)))

    # Trier par moyenne décroissante
    scores.sort(key=lambda x: x[1], reverse=True)
    evalues_count = len(scores)

    target_rank = None
    for rank, (ins_id, moy, cnt) in enumerate(scores, 1):
        if ins_id == target_inscription_id:
            target_rank = rank
            break

    return target_rank, evalues_count


def generer_ou_recuperer_bulletin(ecole_id, annee, user, inscription_id, periode=None, appreciation_generale=None):
    """
    Génère ou récupère un enregistrement Bulletin persistant pour une inscription et période.
    - Année active : création / recalcul autorisé et persisté.
    - Année archivée : renvoie le bulletin existant figé ou calcul en lecture seule sans mutation.
    - Année planifiée : génération interdite.
    """
    if not annee:
        return None, "Année scolaire non définie."

    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=ecole_id).first()
    if not inscription:
        return None, "Inscription introuvable ou non autorisée."

    periode_nom = periode or "Trimestre 1"

    # Vérification année planifiée
    if annee.statut == "planifiee":
        return None, MESSAGE_ANNEE_PLANIFIEE

    # Année archivée : lecture seule stricte
    if annee.statut == "archivee":
        bulletin_existant = Bulletin.query.filter_by(
            inscription_id=inscription.id,
            periode=periode_nom
        ).first()
        if bulletin_existant:
            return bulletin_existant, None

        # Si aucun bulletin figé n'a été préalablement persisté, calculer un instantané virtuel non modifiable
        data, err = calculer_bulletin_data(ecole_id, annee, inscription, periode=periode_nom)
        if err:
            return None, err

        bulletin_virtuel = Bulletin(
            inscription_id=inscription.id,
            eleve_id=inscription.eleve_id,
            ecole_id=ecole_id,
            classe_id=inscription.classe_id,
            annee_scolaire_id=inscription.annee_scolaire_id,
            periode=periode_nom,
            moyenne_generale=data['moyenne_generale'],
            rang=data['rang'],
            rang_total=data['rang_total'],
            appreciation_generale=appreciation_generale or data['appreciation'],
            statut='valide',
            created_at=datetime.utcnow()
        )
        return bulletin_virtuel, None

    # Année active : calcul et persistance
    data, err = calculer_bulletin_data(ecole_id, annee, inscription, periode=periode_nom)
    if err:
        return None, err

    bulletin = Bulletin.query.filter_by(
        inscription_id=inscription.id,
        periode=periode_nom
    ).first()

    if not bulletin:
        bulletin = Bulletin(
            inscription_id=inscription.id,
            eleve_id=inscription.eleve_id,
            ecole_id=ecole_id,
            classe_id=inscription.classe_id,
            annee_scolaire_id=inscription.annee_scolaire_id,
            periode=periode_nom,
            moyenne_generale=data['moyenne_generale'],
            rang=data['rang'],
            rang_total=data['rang_total'],
            appreciation_generale=appreciation_generale or data['appreciation'],
            statut='valide',
            created_at=datetime.utcnow()
        )
        db.session.add(bulletin)
    else:
        bulletin.moyenne_generale = data['moyenne_generale']
        bulletin.rang = data['rang']
        bulletin.rang_total = data['rang_total']
        if appreciation_generale is not None:
            bulletin.appreciation_generale = appreciation_generale
        bulletin.updated_at = datetime.utcnow()

    db.session.commit()
    return bulletin, None


def modifier_appreciation_bulletin(ecole_id, annee, user, bulletin_id, nouvelle_appreciation):
    """Modifie l'appréciation générale d'un bulletin (réservé à l'année active)."""
    if not annee or annee.statut == "archivee":
        return None, "Cette année est archivée : modification interdite (lecture seule)."
    if annee.statut == "planifiee":
        return None, MESSAGE_ANNEE_PLANIFIEE

    bulletin = Bulletin.query.filter_by(id=bulletin_id, ecole_id=ecole_id).first()
    if not bulletin:
        return None, "Bulletin introuvable."

    bulletin.appreciation_generale = (nouvelle_appreciation or '').strip()
    bulletin.updated_at = datetime.utcnow()
    db.session.commit()
    return bulletin, None


def supprimer_bulletin(ecole_id, annee, user, bulletin_id):
    """Supprime un bulletin (strictement interdit sur année archivée ou planifiée)."""
    if not annee or annee.statut == "archivee":
        return False, "Cette année est archivée : suppression interdite (lecture seule)."
    if annee.statut == "planifiee":
        return False, "Opération non autorisée sur une année planifiée."
    if user.role != "admin":
        return False, "Seul un administrateur peut supprimer un bulletin."

    bulletin = Bulletin.query.filter_by(id=bulletin_id, ecole_id=ecole_id).first()
    if not bulletin:
        return False, "Bulletin introuvable."

    db.session.delete(bulletin)
    db.session.commit()
    return True, None

