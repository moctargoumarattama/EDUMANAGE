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
from app.services.notes_annuelles import (
    SEMESTRE_1,
    SEMESTRE_2,
    PERIODES_SEMESTRES,
    TYPE_COMPOSITION,
    TYPES_CONTROLE_CONTINU,
    calculer_moyenne_controles,
    calculer_moyenne_matiere_semestre,
    calculer_points_matiere,
    calculer_moyenne_generale_semestre,
    calculer_moyenne_annuelle,
)
from app.services.semestres import compter_absences_semestre, compter_retards_semestre


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


def _get_appreciation(moyenne):
    if moyenne is None:
        return "En attente"
    if moyenne >= 16:
        return "Excellent"
    if moyenne >= 14:
        return "Très bien"
    if moyenne >= 12:
        return "Bien"
    if moyenne >= 10:
        return "Assez bien"
    return "Insuffisant"


def calculer_bulletin_data(ecole_id, annee, inscription, periode=None):
    """
    Calcule toutes les données académiques pour le bulletin semestriel d'une Inscription (Phase 5D).
    Modèle à 2 semestres :
    - Moyenne contrôles (Devoir, Interrogation) = simple moyenne arithmétique.
    - Note composition (max 1 par matière/semestre).
    - Moyenne semestre matière = (moyenne_controles + note_composition) / 2 (None si incomplet).
    - Coefficient officiel = Cours.coefficient.
    - Points matière = moyenne_semestre * Cours.coefficient.
    - Moyenne générale semestrielle = sum(points) / sum(coefficients) des matières finalisées.
    - Statistiques de classe semestrielles (rang, effectif, moyenne classe, min, max).
    """
    if not inscription or inscription.ecole_id != ecole_id:
        return None, "Inscription introuvable ou non autorisée."

    target_periode = periode or SEMESTRE_1

    # Sélection des notes de cette inscription pour la période demandée
    q = Note.query.options(joinedload(Note.cours).joinedload(Cours.professeur)).filter(
        Note.inscription_id == inscription.id,
        Note.ecole_id == ecole_id,
        Note.periode == target_periode
    )

    notes = q.order_by(Note.cours_id, Note.date_evaluation.desc()).all()

    # Regroupement des notes par cours
    notes_par_cours = defaultdict(list)
    for n in notes:
        cours_nom = n.cours.nom if n.cours else (n.matiere or "Non renseigné")
        notes_par_cours[cours_nom].append(n)

    disciplines = []
    moyennes_par_cours = {}
    coefficients_par_cours = {}
    points_par_cours = {}

    for cours_nom, c_notes in sorted(notes_par_cours.items(), key=lambda x: (x[0] or "").lower()):
        cours = c_notes[0].cours if (c_notes and c_notes[0].cours) else None
        cours_coef = cours.coefficient if (cours and cours.coefficient) else 1.0

        controles = [n for n in c_notes if n.type_evaluation in TYPES_CONTROLE_CONTINU]
        comp = next((n for n in c_notes if n.type_evaluation == TYPE_COMPOSITION), None)

        moy_controles = calculer_moyenne_controles(controles)
        note_comp = comp.valeur if comp else None
        moy_semestre = calculer_moyenne_matiere_semestre(moy_controles, note_comp)
        pts = calculer_points_matiere(moy_semestre, cours_coef)

        prof_nom = "Non assigné"
        if cours and cours.professeur:
            p = cours.professeur
            prof_nom = f"{p.prenom or ''} {p.nom or ''}".strip() or "Non assigné"

        apprec_disc = _get_appreciation(moy_semestre)

        disciplines.append({
            'cours': cours,
            'cours_nom': cours_nom,
            'professeur_nom': prof_nom,
            'moyenne_controles': moy_controles,
            'note_composition': note_comp,
            'moyenne_semestre': moy_semestre,
            'coefficient': cours_coef,
            'points': pts,
            'est_finalisee': (moy_semestre is not None),
            'appreciation': apprec_disc,
            'notes_controles': controles,
            'notes': c_notes,
        })

        moyennes_par_cours[cours_nom] = moy_semestre
        coefficients_par_cours[cours_nom] = cours_coef
        points_par_cours[cours_nom] = pts

    # Calcul de complétude canonique via evaluations.py
    from app.services.evaluations import (
        calculer_completude_inscription,
        calculer_stats_et_classements_classe,
        STATUS_COMPLETE,
        STATUS_PROVISOIRE,
        STATUS_NON_EVALUE,
    )

    eval_info = calculer_completude_inscription(ecole_id, annee.id, inscription, periode=target_periode)

    total_points = round(sum(d['points'] for d in disciplines if d['est_finalisee']), 2)
    total_coefs = sum(d['coefficient'] for d in disciplines if d['est_finalisee'])
    moyenne_generale = eval_info["average"]

    # Mention / appréciation globale basée sur la complétude
    if eval_info["status"] == STATUS_COMPLETE:
        if moyenne_generale is not None:
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
    elif eval_info["status"] == STATUS_PROVISOIRE:
        appreciation = f"En cours ({eval_info['evaluated_subjects']}/{eval_info['expected_subjects']} matières)"
        appreciation_code = "provisoire"
        badge_class = "badge-mention-provisoire bg-warning text-dark"
    else:
        appreciation = "Non évalué"
        appreciation_code = "non-evalue"
        badge_class = "badge-mention-non-evalue bg-secondary text-white"

    # Calcul du rang et des statistiques de classe pour le semestre via service centralisé
    stats_classe_raw = calculer_stats_et_classements_classe(
        ecole_id,
        inscription.classe_id,
        inscription.annee_scolaire_id,
        periode=target_periode
    )

    rang = stats_classe_raw["rangs_par_inscription"].get(inscription.id)
    rang_total = stats_classe_raw["complets_count"]
    stats_classe = {
        'effectif_classe': stats_classe_raw['effectif_total'],
        'evalues_count': stats_classe_raw['complets_count'],
        'moyenne_classe': stats_classe_raw['moyenne_classe_officielle'],
        'plus_forte_moyenne': stats_classe_raw['plus_forte_moyenne'],
        'plus_faible_moyenne': stats_classe_raw['plus_faible_moyenne'],
        'taux_reussite': stats_classe_raw['taux_reussite'],
    }

    nb_absences = compter_absences_semestre(ecole_id, annee.id, inscription.id, target_periode)
    nb_retards = compter_retards_semestre(ecole_id, annee.id, inscription.id, target_periode)

    data = {
        'inscription': inscription,
        'eleve': inscription.eleve,
        'classe': inscription.classe,
        'annee_scolaire': inscription.annee_scolaire,
        'periode': target_periode,
        'notes': notes,
        'disciplines': disciplines,
        'notes_par_cours': dict(notes_par_cours),
        'moyennes_par_cours': moyennes_par_cours,
        'coefficients_par_cours': coefficients_par_cours,
        'points_par_cours': points_par_cours,
        'total_coefficients': total_coefs,
        'total_points': total_points,
        'moyenne_generale': moyenne_generale,
        'eval_info': eval_info,
        'statut_completude': eval_info['status'],
        'est_provisoire': (eval_info['status'] == STATUS_PROVISOIRE),
        'est_complet': (eval_info['status'] == STATUS_COMPLETE),
        'notes_count': len(notes),
        'rang': rang,
        'rang_total': rang_total,
        'effectif_classe': stats_classe_raw['effectif_total'],
        'stats_classe': stats_classe,
        'nb_absences': nb_absences,
        'nb_retards': nb_retards,
        'calendrier_semestres_configure': nb_absences is not None,
        'appreciation': appreciation,
        'appreciation_code': appreciation_code,
        'badge_class': badge_class,
    }
    return data, None


def _calculer_rang_et_stats_classe(ecole_id, classe_id, annee_scolaire_id, target_inscription_id, periode=SEMESTRE_1):
    from app.services.evaluations import calculer_stats_et_classements_classe
    stats = calculer_stats_et_classements_classe(ecole_id, classe_id, annee_scolaire_id, periode=periode)
    target_rank = stats["rangs_par_inscription"].get(target_inscription_id)
    return target_rank, stats["complets_count"], {
        'effectif_classe': stats["effectif_total"],
        'evalues_count': stats["complets_count"],
        'moyenne_classe': stats["moyenne_classe_officielle"],
        'plus_forte_moyenne': stats["plus_forte_moyenne"],
        'plus_faible_moyenne': stats["plus_faible_moyenne"],
        'taux_reussite': stats["taux_reussite"],
    }


def _calculer_rang_classe(ecole_id, classe_id, annee_scolaire_id, target_inscription_id, periode=None):
    """Pour rétrocompatibilité."""
    rank, total, _ = _calculer_rang_et_stats_classe(ecole_id, classe_id, annee_scolaire_id, target_inscription_id, periode)
    return rank, total


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

    periode_nom = periode or SEMESTRE_1

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
