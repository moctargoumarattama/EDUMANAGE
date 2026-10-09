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
from sqlalchemy import or_
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


STATUTS_BULLETIN_SCELLES = {
    'archive', 'archivé', 'archivee', 'archivée', 'verrouille', 'verrouillé'
}


def bulletin_est_archive(bulletin):
    """Détermine l'immutabilité depuis le bulletin et son année réelle."""
    if not bulletin:
        return False
    statut = str(bulletin.statut or '').strip().casefold()
    inscription = bulletin.inscription
    annee = inscription.annee_scolaire if inscription else bulletin.annee_scolaire
    return statut in STATUTS_BULLETIN_SCELLES or bool(annee and annee.statut == 'archivee')


def periode_publiee_pour_inscription(inscription, periode_nom):
    """Contrôle la publication du semestre exact de l'inscription."""
    if not inscription or not periode_nom:
        return False
    periode = PeriodeBulletin.query.filter_by(
        ecole_id=inscription.ecole_id,
        annee_id=inscription.annee_scolaire_id,
        nom=periode_nom,
        publie=True,
    ).first()
    if not periode:
        return False
    bulletin = Bulletin.query.filter_by(
        ecole_id=inscription.ecole_id,
        inscription_id=inscription.id,
        periode=periode_nom,
    ).first()
    if inscription.annee_scolaire and inscription.annee_scolaire.statut == 'archivee' and not bulletin:
        return False
    return not bulletin or str(bulletin.statut or '').strip().casefold() not in {
        'brouillon', 'en_cours', 'provisoire'
    }


def verifier_publication_periode(periode):
    """Refuse une publication sans notes ou avec des évaluations incomplètes."""
    autorise, erreur = verifier_modification_periode(periode)
    if not autorise:
        return False, erreur

    classes = Classe.query.filter_by(
        ecole_id=periode.ecole_id, annee_scolaire_id=periode.annee_id
    ).all()
    classes_avec_eleves = 0
    for classe in classes:
        inscriptions = Inscription.query.filter_by(
            ecole_id=periode.ecole_id, annee_scolaire_id=periode.annee_id,
            classe_id=classe.id,
        ).with_entities(Inscription.id).all()
        if not inscriptions:
            continue
        classes_avec_eleves += 1
        ids = [row.id for row in inscriptions]
        notes_count = Note.query.join(Cours, Note.cours_id == Cours.id).filter(
            Note.ecole_id == periode.ecole_id,
            Note.inscription_id.in_(ids),
            Note.periode == periode.nom,
            Note.valeur.isnot(None),
            Cours.classe_id == classe.id,
        ).count()
        if notes_count == 0:
            return False, f"Impossible de publier : aucune note n'est saisie pour la classe {classe.nom} sur cette période."
    if not classes_avec_eleves:
        return False, "Impossible de publier : aucun élève inscrit dans cette année."

    from app.services.evaluations import verifier_eligibilite_publication_periode
    avis = verifier_eligibilite_publication_periode(
        periode.ecole_id, periode.annee_id, periode.nom
    )
    if not avis['eligible']:
        return False, avis['message_resume']
    return True, None


def verifier_modification_periode(periode):
    """Empêche la mutation d'une année ou d'un bulletin déjà archivé."""
    if not periode or not periode.annee or periode.annee.statut != 'active':
        return False, "Une période d'une année archivée ou planifiée est en lecture seule."
    bulletins = Bulletin.query.outerjoin(
        Inscription, Bulletin.inscription_id == Inscription.id
    ).filter(
        Bulletin.ecole_id == periode.ecole_id,
        Bulletin.periode == periode.nom,
        or_(
            Bulletin.annee_scolaire_id == periode.annee_id,
            Inscription.annee_scolaire_id == periode.annee_id,
        ),
    ).all()
    if any(bulletin_est_archive(bulletin) for bulletin in bulletins):
        return False, "Un bulletin de cette période est archivé : modification interdite."
    return True, None


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
            classes_cours_ids = [
                row.classe_id for row in Cours.query.with_entities(Cours.classe_id)
                .join(Classe, Classe.id == Cours.classe_id)
                .filter(
                    Cours.professeur_id == professeur.id,
                    Cours.ecole_id == ecole_id,
                    Cours.classe_id.isnot(None),
                    Classe.ecole_id == ecole_id,
                    Classe.annee_scolaire_id == annee.id,
                ).all()
            ]
            allowed_class_ids = set(classes_cours_ids)
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
        return "Non évalué"
    if moyenne >= 16:
        return "Excellent"
    if moyenne >= 14:
        return "Très bien"
    if moyenne >= 12:
        return "Bien"
    if moyenne >= 10:
        return "Assez bien"
    return "Insuffisant"


def calculer_bulletin_data(ecole_id, annee, inscription, periode=None, periode_publiee=None):
    """
    Calcule toutes les données académiques pour le bulletin semestriel d'une Inscription (Phase 5D).
    Modèle à 2 semestres :
    - Moyenne contrôles (Devoir, Interrogation) = simple moyenne arithmétique.
    - Note composition (max 1 par matière/semestre).
    - Moyenne semestre matière = (moyenne_controles + note_composition) / 2 (sans comp : moy contrôles).
    - Coefficient officiel = Cours.coefficient.
    - Points matière = moyenne_semestre * Cours.coefficient.
    - Moyenne générale semestrielle = sum(points) / sum(coefficients) des matières finalisées.
    - Les matières non évaluées (dispense, absence, saisie en attente) sont mentionnées "Non évalué"
      et leur coefficient est retiré du diviseur pour ne pas pénaliser indûment l'élève.
    - Le bulletin n'est FINAL (officiel) SSI toutes les matières attendues sont notées ET la période est publiée.
    """
    if not inscription or inscription.ecole_id != ecole_id:
        return None, "Inscription introuvable ou non autorisée."

    target_periode = periode or SEMESTRE_1
    from app.services.inscriptions_annuelles import classe_effective_pour_periode
    classe_periode_id = classe_effective_pour_periode(inscription, target_periode)

    if periode_publiee is None:
        p_obj = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id,
            nom=target_periode
        ).first()
        periode_publiee = bool(p_obj and p_obj.publie)

    # Sélection des notes de cette inscription pour la période demandée (restreintes à la classe)
    q = Note.query.options(joinedload(Note.cours).joinedload(Cours.professeur)).filter(
        Note.inscription_id == inscription.id,
        Note.ecole_id == ecole_id,
        Note.periode == target_periode
    )
    if classe_periode_id:
        q = q.join(Cours, Note.cours_id == Cours.id).filter(Cours.classe_id == classe_periode_id)

    notes = q.order_by(Note.cours_id, Note.date_evaluation.desc()).all()

    # Regroupement des notes par cours_id et cours_nom
    notes_par_cours = defaultdict(list)
    notes_par_cours_id = defaultdict(list)
    for n in notes:
        cours_nom = n.cours.nom if n.cours else (n.matiere or "Non renseigné")
        notes_par_cours[cours_nom].append(n)
        if n.cours_id:
            notes_par_cours_id[n.cours_id].append(n)

    from app.services.evaluations import get_cours_attendus_classe, calculer_moyenne_matiere
    cours_attendus = get_cours_attendus_classe(ecole_id, classe_periode_id, annee.id) if classe_periode_id else []

    disciplines = []
    moyennes_par_cours = {}
    coefficients_par_cours = {}
    points_par_cours = {}

    vus_cours_ids = set()

    # 1. Traitement des cours officiels attendus pour la classe
    for cours in cours_attendus:
        vus_cours_ids.add(cours.id)
        cours_nom = cours.nom
        cours_coef = cours.coefficient if (cours.coefficient and cours.coefficient > 0) else 1.0
        c_notes = notes_par_cours_id.get(cours.id, [])

        prof_nom = "Non assigné"
        if cours.professeur:
            p = cours.professeur
            prof_nom = f"{p.prenom or ''} {p.nom or ''}".strip() or "Non assigné"

        if c_notes:
            controles = [n for n in c_notes if n.type_evaluation in TYPES_CONTROLE_CONTINU]
            from app.services.notes_annuelles import est_evaluation_sommative
            comp = next((n for n in c_notes if est_evaluation_sommative(n.type_evaluation)), None)

            moy_controles = calculer_moyenne_controles(controles) if controles else None
            note_comp = comp.valeur if comp else None
            moy_semestre = calculer_moyenne_matiere(c_notes)
            pts = calculer_points_matiere(moy_semestre, cours_coef) if moy_semestre is not None else None
            est_fin = (moy_semestre is not None)
            apprec_disc = _get_appreciation(moy_semestre)
        else:
            controles = []
            comp = None
            moy_controles = None
            note_comp = None
            moy_semestre = None
            pts = None
            est_fin = False
            apprec_disc = "Non évalué"

        disciplines.append({
            'cours': cours,
            'cours_nom': cours_nom,
            'professeur_nom': prof_nom,
            'moyenne_controles': moy_controles,
            'note_composition': note_comp,
            'moyenne_semestre': moy_semestre,
            'coefficient': cours_coef,
            'points': pts,
            'est_finalisee': est_fin,
            'appreciation': apprec_disc,
            'notes_controles': controles,
            'notes': c_notes,
        })

        moyennes_par_cours[cours_nom] = moy_semestre
        coefficients_par_cours[cours_nom] = cours_coef
        points_par_cours[cours_nom] = pts

    # 2. Traitement d'éventuels cours hors liste officielle ayant des notes
    for n in notes:
        if n.cours_id and n.cours_id not in vus_cours_ids:
            vus_cours_ids.add(n.cours_id)
            cours = n.cours
            cours_nom = cours.nom if cours else (n.matiere or "Non renseigné")
            cours_coef = cours.coefficient if (cours and cours.coefficient) else 1.0
            c_notes = notes_par_cours_id.get(n.cours_id, [n])
            moy_semestre = calculer_moyenne_matiere(c_notes)
            pts = calculer_points_matiere(moy_semestre, cours_coef) if moy_semestre is not None else None
            disciplines.append({
                'cours': cours,
                'cours_nom': cours_nom,
                'professeur_nom': "Non assigné",
                'moyenne_controles': None,
                'note_composition': None,
                'moyenne_semestre': moy_semestre,
                'coefficient': cours_coef,
                'points': pts,
                'est_finalisee': (moy_semestre is not None),
                'appreciation': _get_appreciation(moy_semestre),
                'notes_controles': [],
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

    eval_info = calculer_completude_inscription(
        ecole_id, annee.id, inscription,
        periode=target_periode,
        periode_publiee=periode_publiee
    )

    # Neutralisation des matières non évaluées :
    # Seules les matières évaluées contribuent au total des points et au diviseur des coefficients
    total_points = round(sum(d['points'] for d in disciplines if d['est_finalisee'] and d['points'] is not None), 2)
    total_coefs = sum(d['coefficient'] for d in disciplines if d['est_finalisee'])
    moyenne_generale = eval_info["average"]

    # Mention / appréciation globale basée sur la complétude officielle
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
        appreciation = "En attente"
        appreciation_code = "provisoire"
        badge_class = "badge-mention-provisoire bg-warning text-dark"
    else:
        appreciation = "Non évalué"
        appreciation_code = "non-evalue"
        badge_class = "badge-mention-non-evalue bg-secondary text-white"

    # Calcul du rang et des statistiques de classe pour le semestre via service centralisé
    stats_classe_raw = calculer_stats_et_classements_classe(
        ecole_id,
        classe_periode_id,
        inscription.annee_scolaire_id,
        periode=target_periode,
        periode_publiee=periode_publiee
    )

    rang = stats_classe_raw["rangs_par_inscription"].get(inscription.id) if eval_info["status"] == STATUS_COMPLETE else None
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
        'classe': db.session.get(Classe, classe_periode_id) if classe_periode_id else None,
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
    - Année archivée : renvoie uniquement le bulletin existant figé.
    - Année planifiée : génération interdite.
    """
    if not annee:
        return None, "Année scolaire non définie."

    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=ecole_id).first()
    if not inscription:
        return None, "Inscription introuvable ou non autorisée."
    if inscription.annee_scolaire_id != annee.id:
        return None, "L'inscription n'appartient pas à l'année scolaire demandée."

    periode_nom = periode or SEMESTRE_1

    if annee.statut == "planifiee":
        return None, MESSAGE_ANNEE_PLANIFIEE

    bulletin_existant = Bulletin.query.filter_by(
        ecole_id=ecole_id, inscription_id=inscription.id, periode=periode_nom
    ).first()
    periode_close = PeriodeBulletin.query.filter_by(
        ecole_id=ecole_id, annee_id=annee.id, nom=periode_nom, publie=True
    ).first()
    if bulletin_existant and (
        periode_close or str(bulletin_existant.statut or '').casefold() in
        {'publie', 'publié', 'archive', 'archivé', 'archivee', 'archivée', 'verrouille', 'verrouillé'}
        or bool(getattr(bulletin_existant, 'verrouille', False))
    ):
        return bulletin_existant, None

    # Année archivée : lecture seule stricte
    if annee.statut == "archivee":
        bulletin_existant = Bulletin.query.filter_by(
            ecole_id=ecole_id, inscription_id=inscription.id,
            periode=periode_nom
        ).first()
        if bulletin_existant:
            return bulletin_existant, None

        return None, "Bulletin archivé introuvable : recalcul interdit."

    # Année active : calcul et persistance
    data, err = calculer_bulletin_data(ecole_id, annee, inscription, periode=periode_nom)
    if err:
        return None, err

    bulletin = Bulletin.query.filter_by(
        ecole_id=ecole_id, inscription_id=inscription.id,
        periode=periode_nom
    ).first()

    if not bulletin:
        bulletin = Bulletin(
            inscription_id=inscription.id,
            eleve_id=inscription.eleve_id,
            ecole_id=ecole_id,
            classe_id=data['classe'].id if data['classe'] else inscription.classe_id,
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
    annee_bulletin_id = bulletin.inscription.annee_scolaire_id if bulletin.inscription else bulletin.annee_scolaire_id
    if bulletin_est_archive(bulletin) or annee_bulletin_id != annee.id:
        return None, "Bulletin archivé ou d'une autre année : modification interdite."
    if user.role == 'professeur':
        professeur = getattr(user, 'professeur_rel', None)
        classe_id = bulletin.inscription.classe_id if bulletin.inscription else bulletin.classe_id
        if not professeur or not classe_id or not (
            professeur.classes_assignees.filter_by(id=classe_id, ecole_id=ecole_id).first()
            or Cours.query.filter_by(
                ecole_id=ecole_id, classe_id=classe_id, professeur_id=professeur.id
            ).first()
        ):
            return None, "Ce bulletin n'appartient pas à une classe autorisée pour ce professeur."

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
    annee_bulletin_id = bulletin.inscription.annee_scolaire_id if bulletin.inscription else bulletin.annee_scolaire_id
    if bulletin_est_archive(bulletin) or annee_bulletin_id != annee.id:
        return False, "Bulletin archivé ou d'une autre année : suppression interdite."

    db.session.delete(bulletin)
    db.session.commit()
    return True, None


def calculer_moyenne_annuelle_reglementaire(inscription_id, ecole_id):
    """
    Calcule la moyenne annuelle réglementaire d'un élève pour une inscription donnée.
    Règle académique stricte :
    - La division s'effectue obligatoirement par le nombre réglementaire de périodes officielles
      (ex. 2 semestres ou 3 trimestres configurés pour l'année scolaire).
    - Si une ou plusieurs périodes manquent à l'évaluation :
      1. Statut = 'Dossier Incomplet'
      2. cursus_incomplet = True
      3. Suggestion = 'Décision réservée au conseil (cursus incomplet)'
    """
    vide = {
        "moyenne_annuelle": None,
        "statut": "Dossier Incomplet",
        "statut_deliberation": "Dossier Incomplet",
        "cursus_incomplet": True,
        "nb_periodes_evaluees": 0,
        "nb_periodes_attendues": 0,
        "suggestion": "Décision réservée au conseil (cursus incomplet)",
        "somme_moyennes": 0.0,
        "bulletins": [],
    }
    if not inscription_id or not ecole_id:
        return vide

    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=ecole_id).first()
    if not inscription:
        return vide

    annee = inscription.annee_scolaire
    if annee and getattr(annee, "periodes_bulletin", None):
        periodes_attendues = [p.nom for p in annee.periodes_bulletin]
    else:
        periodes_attendues = [SEMESTRE_1, SEMESTRE_2]

    nb_attendues = max(1, len(periodes_attendues))

    bulletins = Bulletin.query.filter_by(
        ecole_id=ecole_id,
        inscription_id=inscription.id,
    ).filter(Bulletin.moyenne_generale.isnot(None)).all()

    bulletins_map = {b.periode: b for b in bulletins if b.periode}
    bulletins_utiles = [bulletins_map[p] for p in periodes_attendues if p in bulletins_map]

    nb_evaluees = len(bulletins_utiles)
    somme = sum(float(b.moyenne_generale) for b in bulletins_utiles)

    cursus_incomplet = (nb_evaluees < nb_attendues)
    moyenne_annuelle = round(somme / nb_attendues, 2) if nb_evaluees > 0 else None

    if cursus_incomplet:
        statut = "Dossier Incomplet"
        suggestion = "Décision réservée au conseil (cursus incomplet)"
    else:
        statut = "Complet"
        if moyenne_annuelle is not None:
            suggestion = "Passage" if moyenne_annuelle >= 10.0 else "Redoublement"
        else:
            suggestion = "Décision réservée au conseil (cursus incomplet)"

    return {
        "moyenne_annuelle": moyenne_annuelle,
        "statut": statut,
        "statut_deliberation": statut,
        "cursus_incomplet": cursus_incomplet,
        "nb_periodes_evaluees": nb_evaluees,
        "nb_periodes_attendues": nb_attendues,
        "suggestion": suggestion,
        "somme_moyennes": round(somme, 2),
        "bulletins": bulletins_utiles,
    }

