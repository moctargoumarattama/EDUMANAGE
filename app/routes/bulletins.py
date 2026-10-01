from collections import defaultdict
from flask import g, jsonify, request
from sqlalchemy import or_
from app.authorization import tenant_required
from . import main
import os
from .common import (
    AnneeScolaire,
    Classe,
    Cours,
    Eleve,
    Note,
    PeriodeBulletin,
    PeriodeForm,
    can_access_eleve,
    bulletins_accessible_pour_parent,
    check_parent_access,
    current_app,
    current_user,
    datetime,
    db,
    flash,
    get_ecole_filter_query,
    joinedload,
    login_required,
    redirect,
    render_template,
    role_required,
    send_file,
    url_for,
)
from app.models import Bulletin, Inscription, JournalCorrection
from app.services import generer_bulletin_pdf, generer_bulletins_classe_pdf
from app.services.annees_scolaires import get_annee_consultee
from app.utils_classes import classes_triees_pedagogique
from app.utils import sanitize_internal_url
from app.services.bulletins_annuels import (
    statut_annee_bulletins,
    bulletins_modifiables,
    get_inscriptions_bulletins,
    calculer_bulletin_data,
    modifier_appreciation_bulletin,
    supprimer_bulletin,
    MESSAGE_ANNEE_PLANIFIEE,
)
from app.services.evaluations import (
    calculer_completude_inscription,
    calculer_stats_et_classements_classe,
    STATUS_COMPLETE,
    STATUS_PROVISOIRE,
    STATUS_NON_EVALUE,
)


_BULLETINS_CONTEXT_ARGS = ('search', 'classe_id', 'mention', 'statut_bulletin', 'periode')


def _bulletins_context_url():
    args = {}
    for key in _BULLETINS_CONTEXT_ARGS:
        value = request.args.get(key)
        if value not in (None, ""):
            args[key] = value
    return url_for('main.bulletins', **args)


def _bulletins_return_url():
    return sanitize_internal_url(
        request.form.get('return_url') or request.args.get('return_url'),
        _bulletins_context_url(),
    )


def _trier_periodes_chronologique(periodes):
    """
    Tri pédagogique et chronologique déterministe des périodes scolaires :
    1. Numéro extrait du nom ("Semestre 1" -> 1, "Trimestre 2" -> 2)
    2. Date de début si disponible
    3. ID en BDD
    """
    import re
    from datetime import date
    def _cle(p):
        match = re.search(r'\d+', p.nom or '')
        num = int(match.group()) if match else None
        d = p.date_debut or date.min
        return (num if num is not None else 999, d, p.id)
    return sorted(periodes, key=_cle)


@main.route('/bulletin_eleve/<int:id>')
@main.route('/bulletin/<int:id>')
@main.route('/bulletins/<int:id>')
@main.route('/bulletin/inscription/<int:inscription_id>')
@login_required
@role_required('admin', 'professeur', 'parent')
@tenant_required
def bulletin_eleve(id=None, inscription_id=None):
    """
    Génère le bulletin PDF d’un élève ancré à son Inscription annuelle :
    - Sécurisé par école et rôle.
    - Accessible aux admins, professeurs et parents autorisés.
    - Année active : génération complète.
    - Année archivée : consultation / téléchargement PDF en lecture seule stricte.
    - Année planifiée : génération interdite.
    - Règle 2C-5D : Ne mute jamais session["annee_consultee"].
    """
    ecole_id = g.ecole_id
    req_inscription_id = inscription_id or request.args.get('inscription_id', type=int)

    inscription = None
    eleve = None
    annee = None

    if req_inscription_id:
        inscription = Inscription.query.filter_by(id=req_inscription_id, ecole_id=ecole_id).first()
        if not inscription:
            flash("Inscription introuvable ou non autorisée pour cette école.", "danger")
            return redirect(url_for('main.bulletins'))
        eleve = inscription.eleve
        annee = inscription.annee_scolaire
    elif id is not None:
        # Vérifier si l'id correspond à un Bulletin existant
        bulletin_db = Bulletin.query.filter_by(id=id, ecole_id=ecole_id).first()
        if bulletin_db and bulletin_db.inscription:
            inscription = bulletin_db.inscription
            eleve = inscription.eleve
            annee = inscription.annee_scolaire
        else:
            # L'id est un eleve_id
            eleve = Eleve.query.filter_by(id=id, ecole_id=ecole_id).first()
            if not eleve:
                flash("Élève introuvable ou appartenant à une autre école.", "danger")
                return redirect(url_for('main.bulletins'))

            annee = get_annee_consultee(ecole_id)
            if not annee:
                flash("Aucune année scolaire active configurée.", "warning")
                return redirect(url_for('main.bulletins'))

            inscription = Inscription.query.filter_by(
                eleve_id=eleve.id,
                annee_scolaire_id=annee.id,
                ecole_id=ecole_id
            ).first()

            if not inscription:
                # Tentative sur la dernière inscription de l'élève
                inscription = Inscription.query.filter_by(
                    eleve_id=eleve.id,
                    ecole_id=ecole_id
                ).order_by(Inscription.id.desc()).first()

                if inscription:
                    annee = inscription.annee_scolaire
                else:
                    flash("L'élève n'est inscrit dans aucune classe pour cette année scolaire.", "danger")
                    return redirect(url_for('main.bulletins'))

    if not inscription or not eleve or not annee:
        flash("Informations du bulletin introuvables.", "danger")
        return redirect(url_for('main.bulletins'))

    # Sécurité parent : vérifier l'accès à l'enfant
    if current_user.role == 'parent':
        if not check_parent_access(eleve.id):
            flash("Accès non autorisé à cet élève.", "danger")
            return redirect(url_for('main.parent_dashboard'))

        # Si année active, vérifier si les bulletins sont publiés
        if annee.statut == 'active' and not bulletins_accessible_pour_parent(eleve.id):
            flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
            return redirect(url_for('main.parent_dashboard'))
        # Pour une année archivée, le parent peut toujours télécharger l'historique

    # Sécurité professeur
    if current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        if professeur:
            classes_prof = [c.id for c in professeur.classes_assignees.filter_by(ecole_id=ecole_id).all()]
            classes_cours = [
                c.id for c in Classe.query.join(Cours)
                .filter(
                    Cours.professeur_id == professeur.id,
                    Cours.ecole_id == ecole_id,
                    Classe.annee_scolaire_id == annee.id
                ).all()
            ]
            allowed_classes = set(classes_prof + classes_cours)
            if inscription.classe_id not in allowed_classes and not can_access_eleve(eleve):
                flash("Vous n'avez pas accès aux bulletins de cet élève.", "danger")
                return redirect(url_for('main.bulletins'))

    # Statut de l'année scolaire : Année planifiée interdite
    if annee.statut == 'planifiee':
        flash(MESSAGE_ANNEE_PLANIFIEE, "warning")
        return redirect(url_for('main.bulletins'))

    # Récupération de la période demandée ou active
    periode_demandee = request.args.get('periode')
    if not periode_demandee:
        periode_active = PeriodeBulletin.query.filter_by(ecole_id=ecole_id, annee_id=annee.id, periode_active=True).first()
        if not periode_active:
            periode_active = PeriodeBulletin.query.filter_by(ecole_id=ecole_id, annee_id=annee.id, publie=True).first()
        periode_demandee = periode_active.nom if periode_active else "Semestre 1"

    p_obj = PeriodeBulletin.query.filter_by(ecole_id=ecole_id, annee_id=annee.id, nom=periode_demandee).first()
    if not p_obj:
        p_obj = PeriodeBulletin(
            nom=periode_demandee,
            ecole_id=ecole_id,
            annee_id=annee.id,
            publie=False
        )
        db.session.add(p_obj)
        db.session.commit()
    periode_est_publiee = bool(p_obj and p_obj.publie)

    # Calcul des données du bulletin strictement depuis Inscription et ses Notes
    data, err = calculer_bulletin_data(ecole_id, annee, inscription, periode=periode_demandee, periode_publiee=periode_est_publiee)
    if err:
        flash(f"Erreur lors du calcul du bulletin : {err}", "danger")
        return redirect(url_for('main.bulletins'))

    ecole = eleve.ecole
    classe_nom = inscription.classe.nom if inscription.classe else "Sans classe"
    annee_nom = inscription.annee_scolaire.nom if inscription.annee_scolaire else annee.nom

    from app.services.bulletin_verification import generer_token_bulletin
    token_bulletin = generer_token_bulletin(ecole_id, inscription.id, p_obj.id)
    verification_url = url_for('main.verifier_bulletin_public', token=token_bulletin, _external=True)

    try:
        buffer = generer_bulletin_pdf(
            eleve=eleve,
            notes_par_cours=data['notes_par_cours'],
            moyennes_par_cours=data['moyennes_par_cours'],
            moyenne_generale=data['moyenne_generale'],
            logo_path=os.path.join(current_app.static_folder, ecole.logo_path) if ecole and ecole.logo_path else None,
            nom_ecole=ecole.nom if ecole else "École non renseignée",
            adresse_ecole=ecole.adresse if ecole else "-",
            contact_ecole=f"Tél: {ecole.telephone or '-'} - Email: {ecole.email or '-'}" if ecole else "-",
            classe_nom=classe_nom,
            annee_scolaire_nom=annee_nom,
            periode_nom=periode_demandee,
            rang=data['rang'],
            rang_total=data['rang_total'],
            appreciation_generale=data['appreciation'],
            disciplines=data.get('disciplines'),
            total_coefficients=data.get('total_coefficients'),
            total_points=data.get('total_points'),
            stats_classe=data.get('stats_classe'),
            nb_absences=data.get('nb_absences'),
            est_provisoire=data.get('est_provisoire', False),
            verification_url=verification_url,
        )

        filename = f"bulletin_{eleve.prenom}_{eleve.nom}_{annee_nom}_{periode_demandee.replace(' ', '_')}.pdf"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
    except Exception as e:
        current_app.logger.error(f"Erreur lors de la génération du bulletin PDF : {e}")
        flash("Erreur lors de la génération du bulletin PDF.", "danger")
        return redirect(url_for('main.bulletins'))


@main.route('/bulletins')
@login_required
@role_required('admin', 'professeur', 'parent')
@tenant_required
def bulletins():
    """
    Page d'accueil des bulletins organisés par classe et année scolaire consultée.
    Règle 2C-5D : Consomme get_annee_consultee(ecole_id) sans modifier la session.
    """
    ecole_id = g.ecole_id
    context_url = _bulletins_return_url()
    annee = get_annee_consultee(ecole_id)

    if not annee:
        flash("Aucune année scolaire active configurée.", "warning")
        return render_template(
            'bulletins.html',
            classes=[],
            eleves_par_classe={},
            classe_stats={},
            eleves_sans_classe=[],
            periode_active=None,
            periode_suivante=None,
            eleves=[],
            moyenne_generale=0,
            meilleure_moyenne=0,
            taux_reussite=0,
            total_eleves=0,
            bulletins_accessibles=False,
            annee_consultee=None,
            bulletins_modifiables=False,
            statut_warning="Aucune année scolaire configurée."
        )

    # 🔒 Vérifier l'accès pour les parents (uniquement si année active)
    if current_user.role == 'parent' and annee.statut == 'active' and not bulletins_accessible_pour_parent():
        flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
        return redirect(url_for('main.parent_dashboard'))

    statut_warning = statut_annee_bulletins(annee)
    est_modifiable = bulletins_modifiables(annee)

    # Si année planifiée : pas de bulletin académique
    if annee.statut == 'planifiee':
        return render_template(
            'bulletins.html',
            classes=[],
            eleves_par_classe={},
            classe_stats={},
            eleves_sans_classe=[],
            periode_active=None,
            periode_suivante=None,
            eleves=[],
            moyenne_generale=0,
            meilleure_moyenne=0,
            taux_reussite=0,
            total_eleves=0,
            bulletins_accessibles=False,
            annee_consultee=annee,
            bulletins_modifiables=False,
            statut_warning=MESSAGE_ANNEE_PLANIFIEE
        )

    # Récupérer les classes de l'année scolaire consultée
    classes = classes_triees_pedagogique(
        Classe.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id
        )
    ).all()
    class_ids = [c.id for c in classes]

    # Pré-chargement des cours par classe pour éviter N+1
    if class_ids:
        if not hasattr(g, '_cours_attendus_cache'):
            g._cours_attendus_cache = {}
        tous_cours = Cours.query.filter(
            Cours.ecole_id == ecole_id,
            Cours.classe_id.in_(class_ids)
        ).order_by(Cours.nom.asc()).all()
        cours_par_classe = defaultdict(list)
        for crs in tous_cours:
            cours_par_classe[crs.classe_id].append(crs)
        for cid in class_ids:
            g._cours_attendus_cache[(ecole_id, cid, annee.id)] = cours_par_classe[cid]

    # Récupérer les inscriptions de cette année
    inscriptions = get_inscriptions_bulletins(ecole_id, annee, current_user)
    inscr_ids = [ins.id for ins in inscriptions]

    # Récupérer la période demandée ou active pour cette année
    periode_id_arg = request.args.get('periode_id', type=int)
    periode_nom_arg = (request.args.get('periode') or '').strip()

    periode_active = None
    if periode_id_arg:
        periode_active = PeriodeBulletin.query.filter_by(
            id=periode_id_arg,
            ecole_id=ecole_id,
            annee_id=annee.id
        ).first()
    elif periode_nom_arg:
        periode_active = PeriodeBulletin.query.filter_by(
            nom=periode_nom_arg,
            ecole_id=ecole_id,
            annee_id=annee.id
        ).first()

    if not periode_active:
        periode_active = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id,
            periode_active=True
        ).first()
    if not periode_active:
        periode_active = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id,
            publie=True
        ).first()
    if not periode_active:
        periode_active = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id
        ).order_by(PeriodeBulletin.id.asc()).first()

    periode_nom = periode_active.nom if periode_active else "Semestre 1"
    periode_publiee = bool(periode_active and periode_active.publie)

    # Détection intelligente de la période suivante pour le workflow "zéro erreur"
    periode_suivante = None
    if annee and periode_active:
        toutes_periodes_annee = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id
        ).all()
        toutes_periodes_annee = _trier_periodes_chronologique(toutes_periodes_annee)
        idx_p = next((i for i, p in enumerate(toutes_periodes_annee) if p.id == periode_active.id), -1)
        if idx_p != -1 and idx_p + 1 < len(toutes_periodes_annee):
            cand = toutes_periodes_annee[idx_p + 1]
            if not cand.periode_active:
                periode_suivante = cand

    # Filtres de recherche
    search = (request.args.get('search') or request.args.get('q') or '').strip().lower()
    classe_id = request.args.get('classe_id', type=int) or request.args.get('classe', type=int)
    mention_filtre = (request.args.get('mention') or '').strip().lower()
    statut_bulletin = (request.args.get('statut_bulletin') or request.args.get('generation') or '').strip().lower()

    # Bulletins existants en DB pour cette école et année
    bulletins_existants = {
        b.inscription_id: b for b in Bulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id
        ).all()
    }

    # Récupération optimisée des notes liées à ces inscriptions restreintes aux cours officiels
    notes_query = (
        Note.query.options(joinedload(Note.cours))
        .join(Cours, Note.cours_id == Cours.id)
        .join(Inscription, Note.inscription_id == Inscription.id)
        .filter(
            Note.inscription_id.in_(inscr_ids),
            Note.ecole_id == ecole_id,
            Note.annee_id == annee.id,
            or_(
                Cours.classe_id == Inscription.classe_id,
                Inscription.classe_id.is_(None)
            )
        )
    )
    if periode_nom:
        notes_query = notes_query.filter(Note.periode == periode_nom)
    notes_all = notes_query.all() if inscr_ids else []

    notes_par_inscription = defaultdict(list)
    for n in notes_all:
        notes_par_inscription[n.inscription_id].append(n)

    # Calcul des moyennes et mentions individuelles basées sur la complétude des évaluations
    eleves_avec_moyennes = []
    for ins in inscriptions:
        eleve = ins.eleve
        has_bulletin = (ins.id in bulletins_existants)

        # Filtre sur la classe
        if classe_id and ins.classe_id != classe_id:
            continue

        # Filtre sur génération de bulletin
        if statut_bulletin in ('non_genere', 'non-genere') and has_bulletin:
            continue
        if statut_bulletin in ('genere', 'valide') and not has_bulletin:
            continue

        student_notes = notes_par_inscription.get(ins.id, [])
        eval_info = calculer_completude_inscription(
            ecole_id,
            annee.id,
            ins,
            periode=periode_nom,
            periode_publiee=periode_publiee,
            notes=student_notes,
        )

        status = eval_info["status"]
        moyenne = eval_info["average"]

        if periode_publiee and moyenne is not None:
            status = STATUS_COMPLETE
            eval_info["status"] = STATUS_COMPLETE
            eval_info["is_official"] = True
            eval_info["is_provisoire"] = False

        if moyenne is not None:
            if moyenne >= 16:
                appreciation = 'Excellent'
                appreciation_code = 'excellent'
                badge_class = 'badge-mention-excellent bg-success text-white'
            elif moyenne >= 14:
                appreciation = 'Très bien'
                appreciation_code = 'tres-bien'
                badge_class = 'badge-mention-tres-bien bg-info text-dark'
            elif moyenne >= 12:
                appreciation = 'Bien'
                appreciation_code = 'bien'
                badge_class = 'badge-mention-bien bg-primary text-white'
            elif moyenne >= 10:
                appreciation = 'Assez bien'
                appreciation_code = 'assez-bien'
                badge_class = 'badge-mention-assez-bien bg-warning text-dark'
            else:
                appreciation = 'Insuffisant'
                appreciation_code = 'insuffisant'
                badge_class = 'badge-mention-insuffisant bg-danger text-white'
        else:
            appreciation = 'Non évalué'
            appreciation_code = 'non-evalue'
            badge_class = 'badge-mention-non-evalue bg-secondary text-white'

        if mention_filtre and appreciation_code != mention_filtre:
            continue

        if search:
            eleve_nom = f"{eleve.prenom} {eleve.nom}".lower() if eleve else ""
            matricule = str(eleve.id if eleve else "")
            code_p = (getattr(eleve, 'code_parent', '') or '').lower() if eleve else ""
            classe_nom = (ins.classe.nom if ins.classe else "").lower()
            if search not in eleve_nom and search not in matricule and search not in code_p and search not in classe_nom:
                continue

        matieres_manquantes = eval_info.get('missing_subjects_names', [])
        matieres_manquantes_count = len(matieres_manquantes)
        is_complet = (matieres_manquantes_count == 0)

        eleves_avec_moyennes.append({
            'inscription': ins,
            'eleve': eleve,
            'classe': ins.classe,
            'annee_scolaire': ins.annee_scolaire,
            'moyenne': moyenne if moyenne is not None else 0,
            'moyenne_raw': moyenne,
            'notes_count': len(student_notes),
            'matieres_count': eval_info['evaluated_subjects'],
            'matieres_manquantes': matieres_manquantes,
            'matieres_manquantes_count': matieres_manquantes_count,
            'is_complet': is_complet,
            'eval_info': eval_info,
            'status': status,
            'is_provisoire': not periode_publiee,
            'is_official': periode_publiee,
            'appreciation': appreciation,
            'appreciation_code': appreciation_code,
            'badge_class': badge_class,
            'notes': student_notes,
            'bulletin_genere': has_bulletin,
            'bulletin_id': bulletins_existants[ins.id].id if has_bulletin else None,
            'rang_classe': None,
            'rang_provisoire': None,
            'rang_classe_total': 0
        })

    # Regroupement des élèves par classe d'inscription annuelle
    eleves_par_classe = {c.id: [] for c in classes}
    eleves_sans_classe = []

    for item in eleves_avec_moyennes:
        ins = item['inscription']
        if ins.classe_id and ins.classe_id in eleves_par_classe:
            eleves_par_classe[ins.classe_id].append(item)
        else:
            eleves_sans_classe.append(item)

    # Calcul des rangs au sein de chaque classe annuelle (temps réel en saisie et officiel à la clôture)
    classe_stats = {}
    for c in classes:
        c_items = eleves_par_classe.get(c.id, [])
        c_evals = [(item['inscription'], item['eval_info']) for item in c_items]
        c_canon_stats = calculer_stats_et_classements_classe(
            ecole_id,
            c.id,
            annee.id,
            periode=periode_nom,
            periode_publiee=periode_publiee,
            precomputed_evals=c_evals,
        )
        rangs_map = c_canon_stats['rangs_par_inscription']

        for item in c_items:
            ins_id = item['inscription'].id
            item['rang_classe'] = rangs_map.get(ins_id)
            item['rang_classe_total'] = c_canon_stats['complets_count']

        c_evalues = [it for it in c_items if it.get('moyenne_raw') is not None]

        # Tri des élèves évalués par moyenne décroissante (avec bris d'égalité déterministe)
        c_evalues.sort(key=lambda x: (
            x['moyenne_raw'] if x['moyenne_raw'] is not None else -1.0,
            -(x['eleve'].id if x.get('eleve') else 0)
        ), reverse=True)

        for rk, it in enumerate(c_evalues, 1):
            if periode_publiee:
                it['rang_classe'] = rk
                it['status'] = STATUS_COMPLETE
                it['is_provisoire'] = False
                it['is_official'] = True
            else:
                it['rang_provisoire'] = rk
                # En mode saisie provisoire, attribuer le rang estimé au fil de l'eau
                it['rang_classe'] = rk
                it['is_provisoire'] = True
                it['is_official'] = False
            it['rang_classe_total'] = len(c_evalues)

        if c_evalues:
            moyennes_vals = [it['moyenne_raw'] for it in c_evalues]
            moy_classe = round(sum(moyennes_vals) / len(moyennes_vals), 2)
            top_classe = max(moyennes_vals)
            pire_classe = min(moyennes_vals)
            admis_c = sum(1 for m in moyennes_vals if m >= 10.0)
            taux_reussite_classe = round((admis_c / len(c_evalues)) * 100.0, 1)
        else:
            moy_classe = None
            top_classe = None
            pire_classe = None
            taux_reussite_classe = None

        complets_items = c_evalues
        non_evalues_items = [it for it in c_items if it.get('moyenne_raw') is None]
        non_evalues_items.sort(key=lambda x: ((x['eleve'].nom or '').lower(), (x['eleve'].prenom or '').lower()))

        eleves_par_classe[c.id] = complets_items + non_evalues_items

        # Analyse des élèves incomplets pour la classe
        eleves_incomplets_liste = []
        for item in c_items:
            if not item.get('is_complet', False):
                eleve_obj = item.get('eleve')
                eleve_nom = f"{eleve_obj.prenom} {eleve_obj.nom}".strip() if eleve_obj else "Élève inconnu"
                eleves_incomplets_liste.append({
                    'nom': eleve_nom,
                    'eleve_id': eleve_obj.id if eleve_obj else None,
                    'count': item.get('matieres_manquantes_count', 0),
                    'matieres': item.get('matieres_manquantes', []),
                    'classe_nom': c.nom,
                })
        eleves_incomplets_count = len(eleves_incomplets_liste)

        classe_stats[c.id] = {
            'classe': c,
            'effectif': len(c_items),
            'evalues_count': len(c_evalues),
            'complets_count': len(c_evalues) if periode_publiee else c_canon_stats.get('complets_count', 0),
            'provisoires_count': 0 if periode_publiee else len(c_evalues),
            'moyenne_classe': moy_classe,
            'meilleure_moyenne': top_classe,
            'pire_moyenne': pire_classe,
            'taux_reussite': taux_reussite_classe,
            'is_officiel': periode_publiee,
            'is_provisoire': not periode_publiee,
            'eleves_incomplets_count': eleves_incomplets_count,
            'eleves_incomplets_liste': eleves_incomplets_liste,
        }

    # Synthèse globale des élèves incomplets pour la modale de clôture
    tous_eleves_incomplets = [el for cs in classe_stats.values() for el in cs.get('eleves_incomplets_liste', [])]
    total_eleves_incomplets = len(tous_eleves_incomplets)

    # Statistiques globales de l'école (calculées en temps réel sur les élèves évalués)
    evalues_globaux = [e for e in eleves_avec_moyennes if e.get('moyenne_raw') is not None]
    if evalues_globaux:
        moyenne_generale = round(sum(e['moyenne_raw'] for e in evalues_globaux) / len(evalues_globaux), 2)
        meilleure_moyenne = max(e['moyenne_raw'] for e in evalues_globaux)
        admis_g = sum(1 for e in evalues_globaux if e['moyenne_raw'] >= 10)
        taux_reussite = round((admis_g / len(evalues_globaux)) * 100, 1)
    else:
        moyenne_generale = None
        meilleure_moyenne = None
        taux_reussite = None

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': len(eleves_avec_moyennes),
            'is_officiel': periode_publiee,
            'is_provisoire': not periode_publiee,
            'total_eleves_incomplets': total_eleves_incomplets,
            'eleves': [
                {
                    'eleve_id': it['eleve'].id,
                    'nom': f"{it['eleve'].prenom} {it['eleve'].nom}",
                    'classe_id': it['classe'].id if it['classe'] else None,
                    'classe_nom': it['classe'].nom if it['classe'] else '',
                    'moyenne': it['moyenne'],
                    'moyenne_raw': it['moyenne_raw'],
                    'appreciation': it['appreciation'],
                    'appreciation_code': it['appreciation_code'],
                    'bulletin_genere': it['bulletin_genere'],
                    'notes_count': it['notes_count'],
                    'matieres_count': it['matieres_count'],
                    'matieres_manquantes': it.get('matieres_manquantes', []),
                    'matieres_manquantes_count': it.get('matieres_manquantes_count', 0),
                    'is_complet': it.get('is_complet', False),
                    'evaluated_subjects': it['eval_info']['evaluated_subjects'],
                    'expected_subjects': it['eval_info']['expected_subjects'],
                    'completion_percent': it['eval_info']['completion_percent'],
                    'rang_classe': it['rang_classe'],
                    'rang_provisoire': it.get('rang_provisoire'),
                    'status': it['status'],
                    'is_provisoire': it.get('is_provisoire', False),
                    'is_officiel': it.get('is_officiel', False)
                }
                for it in eleves_avec_moyennes
            ]
        })

    return render_template(
        'bulletins.html',
        classes=classes,
        eleves_par_classe=eleves_par_classe,
        classe_stats=classe_stats,
        eleves_sans_classe=eleves_sans_classe,
        periode_active=periode_active,
        periode_suivante=periode_suivante,
        eleves=eleves_avec_moyennes,
        moyenne_generale=moyenne_generale,
        meilleure_moyenne=meilleure_moyenne,
        taux_reussite=taux_reussite,
        total_eleves=len(inscriptions),
        total_eleves_incomplets=total_eleves_incomplets,
        tous_eleves_incomplets=tous_eleves_incomplets,
        bulletins_accessibles=bulletins_accessible_pour_parent() or (annee and annee.statut == 'archivee'),
        annee_consultee=annee,
        bulletins_modifiables=est_modifiable,
        statut_warning=statut_warning,
        periode_publiee=periode_publiee,
        is_officiel=periode_publiee,
        is_provisoire=not periode_publiee,
        return_url=context_url
    )


@main.route('/bulletin/<int:id>/supprimer', methods=['POST'])
@main.route('/bulletins/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
@tenant_required
def route_supprimer_bulletin(id):
    context_url = _bulletins_return_url()
    """Supprime un bulletin persistant (interdit sur année archivée ou planifiée)."""
    ecole_id = g.ecole_id
    annee = get_annee_consultee(ecole_id)
    succes, err = supprimer_bulletin(ecole_id, annee, current_user, id)
    if not succes:
        flash(err or "Impossible de supprimer ce bulletin.", "danger")
    else:
        flash("Bulletin supprimé avec succès.", "success")
    return redirect(context_url)


@main.route('/bulletin/<int:id>/appreciation', methods=['POST'])
@login_required
@role_required('admin', 'professeur')
@tenant_required
def route_modifier_appreciation(id):
    context_url = _bulletins_return_url()
    """Modifie l'appréciation générale d'un bulletin (interdit sur année archivée)."""
    ecole_id = g.ecole_id
    annee = get_annee_consultee(ecole_id)
    nouvelle_appreciation = request.form.get('appreciation', '')
    bulletin_mod, err = modifier_appreciation_bulletin(ecole_id, annee, current_user, id, nouvelle_appreciation)
    if err:
        flash(err, "danger")
    else:
        flash("Appréciation mise à jour avec succès.", "success")
    return redirect(context_url)


@main.route('/bulletins/periodes/<int:periode_id>/toggle-publication', methods=['POST'])
@login_required
@role_required('admin', 'directeur')
@tenant_required
def toggle_publication_periode(periode_id):
    """
    Action directe de clôture/publication ou de réouverture d'une période depuis la page des bulletins.
    Sécurisée (POST, CSRF, multi-tenant, rôles admin/directeur).
    """
    ecole_id = g.ecole_id
    periode = PeriodeBulletin.query.filter_by(id=periode_id, ecole_id=ecole_id).first_or_404()

    redirect_args = {}
    classe_id = request.form.get('classe_id', type=int) or request.args.get('classe_id', type=int)
    if classe_id:
        redirect_args['classe_id'] = classe_id
    redirect_args['periode_id'] = periode.id
    search = request.form.get('search') or request.args.get('search')
    if search:
        redirect_args['search'] = search

    if not periode.publie:
        periode.publie = True
        periode.date_publication = datetime.utcnow()
        action_name = "BULLETIN_CLOTURE_OFFICIEL"
        desc = f"Clôture officielle et calcul des rangs pour la période {periode.nom}"
        niveau = "info"
        flash_msg = "Période clôturée avec succès. Les rangs et moyennes officielles sont désormais calculés."
        flash_cat = "success"

        # Invoquer systématiquement le calcul des moyennes et rangs officiels pour chaque classe
        classes_ecole = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=periode.annee_id).all()
        for cl in classes_ecole:
            try:
                calculer_stats_et_classements_classe(
                    ecole_id=ecole_id,
                    classe_id=cl.id,
                    annee_id=periode.annee_id,
                    periode=periode.nom,
                    periode_publiee=True
                )
            except Exception as e:
                current_app.logger.warning(f"Calcul des rangs classe {cl.id} lors de la clôture : {e}")

        # Option : Basculement automatique sur la période suivante dès la clôture
        activer_suivante = request.form.get('activer_periode_suivante') in ['1', 'true', 'on', 'yes']
        if activer_suivante:
            toutes_periodes = PeriodeBulletin.query.filter_by(
                ecole_id=ecole_id,
                annee_id=periode.annee_id
            ).all()
            toutes_periodes = _trier_periodes_chronologique(toutes_periodes)
            idx = next((i for i, p in enumerate(toutes_periodes) if p.id == periode.id), -1)
            if idx != -1 and idx + 1 < len(toutes_periodes):
                suivante = toutes_periodes[idx + 1]
                PeriodeBulletin.query.filter_by(
                    ecole_id=ecole_id,
                    annee_id=periode.annee_id
                ).update({'periode_active': False})
                suivante.periode_active = True
                redirect_args['periode_id'] = suivante.id
                flash_msg = (
                    f"Période clôturée avec succès. Le {suivante.nom} est désormais ACTIF ! "
                    f"Les professeurs peuvent à présent y saisir leurs devoirs et notes."
                )
    else:
        periode.publie = False
        action_name = "BULLETIN_REOUVERT_SAISIE"
        desc = f"Réouverture de la période {periode.nom} pour modifications exceptionnelles"
        niveau = "warning"
        flash_msg = f"Période '{periode.nom}' rouverte pour modification. Les bulletins repassent en saisie provisoire."
        flash_cat = "warning"

    journal = JournalCorrection(
        action=action_name,
        description=desc,
        ecole_id=ecole_id,
        user_id=current_user.id,
        cible_type="periode_bulletin",
        cible_id=periode.id,
        niveau=niveau
    )
    db.session.add(journal)
    db.session.commit()

    flash(flash_msg, flash_cat)
    return redirect(url_for('main.bulletins', **redirect_args))


@main.route('/bulletins/periodes/<int:periode_id>/activer-directement', methods=['POST'])
@login_required
@role_required('admin', 'directeur')
@tenant_required
def activer_directement_periode(periode_id):
    """
    Activation rapide et sécurisée de la période de travail suivante depuis la page des bulletins.
    Bascule immédiatement l'ensemble des enseignants sur la nouvelle période de saisie.
    """
    ecole_id = g.ecole_id
    periode = PeriodeBulletin.query.filter_by(id=periode_id, ecole_id=ecole_id).first_or_404()

    # Désactiver les autres périodes actives de la même année pour cette école
    PeriodeBulletin.query.filter_by(
        ecole_id=ecole_id,
        annee_id=periode.annee_id
    ).update({'periode_active': False})

    periode.periode_active = True

    journal = JournalCorrection(
        action="PERIODE_ACTIVEE_DIRECTEMENT",
        description=f"Activation directe de la période de travail {periode.nom}",
        ecole_id=ecole_id,
        user_id=current_user.id,
        cible_type="periode_bulletin",
        cible_id=periode.id,
        niveau="info"
    )
    db.session.add(journal)
    db.session.commit()

    flash(
        f"Le {periode.nom} est désormais ACTIF ! Les professeurs saisiront désormais leurs devoirs et notes dans cette nouvelle période.",
        "success"
    )

    next_url = request.form.get('next') or request.args.get('next')
    if next_url:
        return redirect(sanitize_internal_url(next_url, url_for('main.index')))

    redirect_args = {'periode_id': periode.id}
    classe_id = request.form.get('classe_id', type=int) or request.args.get('classe_id', type=int)
    if classe_id:
        redirect_args['classe_id'] = classe_id
    search = request.form.get('search') or request.args.get('search')
    if search:
        redirect_args['search'] = search

    return redirect(url_for('main.bulletins', **redirect_args))


@main.route('/bulletins/classes/<int:classe_id>/export-pdf-groupe', methods=['GET'])
@login_required
@role_required('admin', 'directeur', 'secretaire')
@tenant_required
def export_pdf_groupe_classe(classe_id):
    """
    Génère et télécharge un document PDF consolidé unique (multipage)
    regroupant l'ensemble des bulletins de tous les élèves d'une classe
    pour une période donnée.
    """
    import re
    ecole_id = g.ecole_id
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first_or_404()

    # Année scolaire associée
    annee = classe.annee_scolaire
    if not annee:
        annee = get_annee_consultee(ecole_id)
    if not annee:
        flash("Aucune année scolaire active ou consultée configurée.", "warning")
        return redirect(url_for('main.bulletins'))

    if annee.statut == 'planifiee':
        flash(MESSAGE_ANNEE_PLANIFIEE, "warning")
        return redirect(url_for('main.bulletins', classe_id=classe.id))

    # Période demandée, active ou par défaut
    periode_id_arg = request.args.get('periode_id', type=int)
    periode_nom_arg = (request.args.get('periode') or '').strip()

    periode_obj = None
    if periode_id_arg:
        periode_obj = PeriodeBulletin.query.filter_by(
            id=periode_id_arg,
            ecole_id=ecole_id,
            annee_id=annee.id
        ).first()
    elif periode_nom_arg:
        periode_obj = PeriodeBulletin.query.filter_by(
            nom=periode_nom_arg,
            ecole_id=ecole_id,
            annee_id=annee.id
        ).first()

    if not periode_obj:
        periode_obj = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id,
            periode_active=True
        ).first()
    if not periode_obj:
        periode_obj = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id,
            publie=True
        ).first()
    if not periode_obj:
        periode_obj = PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id,
            annee_id=annee.id
        ).order_by(PeriodeBulletin.id.asc()).first()

    if not periode_obj:
        flash("Aucune période d'évaluation disponible pour cette année scolaire.", "warning")
        return redirect(url_for('main.bulletins', classe_id=classe.id))

    periode_nom = periode_obj.nom
    periode_est_publiee = bool(periode_obj.publie)

    # Récupération de tous les élèves inscrits dans cette classe pour l'année scolaire
    inscriptions = (
        Inscription.query.options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
            joinedload(Inscription.annee_scolaire),
        )
        .filter(
            Inscription.classe_id == classe.id,
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee.id,
        )
        .all()
    )

    if not inscriptions:
        flash(f"Aucun élève n'est inscrit dans la classe {classe.nom} pour l'année scolaire sélectionnée.", "warning")
        return redirect(url_for('main.bulletins', classe_id=classe.id, periode_id=periode_obj.id))

    # Cas limite : vérifier s'il existe des notes saisies pour cette classe sur la période
    inscr_ids = [ins.id for ins in inscriptions]
    notes_count = Note.query.filter(
        Note.inscription_id.in_(inscr_ids),
        Note.ecole_id == ecole_id,
        Note.annee_id == annee.id,
        Note.periode == periode_nom
    ).count()

    if notes_count == 0:
        flash(f"Aucune note n'a encore été saisie pour la classe {classe.nom} sur la période {periode_nom}.", "warning")
        return redirect(url_for('main.bulletins', classe_id=classe.id, periode_id=periode_obj.id))

    # Calcul des bulletins individuels
    eleves_data = []
    for ins in inscriptions:
        eleve = ins.eleve
        if not eleve:
            continue
        data, err = calculer_bulletin_data(
            ecole_id,
            annee,
            ins,
            periode=periode_nom,
            periode_publiee=periode_est_publiee
        )
        if err or not data:
            continue

        eleves_data.append({
            'inscription': ins,
            'eleve': eleve,
            'data': data,
            'rang': data.get('rang'),
            'moyenne': data.get('moyenne_generale'),
        })

    if not eleves_data:
        flash(f"Impossible de calculer les bulletins pour la classe {classe.nom}.", "danger")
        return redirect(url_for('main.bulletins', classe_id=classe.id, periode_id=periode_obj.id))

    # Ordonnancement des élèves :
    # Si période clôturée/publiée : trier par rang officiel (les élèves classés en 1er, puis moyenne desc, puis nom)
    # Sinon : trier par ordre alphabétique (nom, prénom)
    if periode_est_publiee:
        eleves_data.sort(
            key=lambda item: (
                0 if item['rang'] is not None else 1,
                item['rang'] if item['rang'] is not None else 999999,
                -(item['moyenne'] if item['moyenne'] is not None else -1),
                (item['eleve'].nom or '').lower(),
                (item['eleve'].prenom or '').lower(),
            )
        )
    else:
        eleves_data.sort(
            key=lambda item: (
                (item['eleve'].nom or '').lower(),
                (item['eleve'].prenom or '').lower(),
            )
        )

    # Préparation des paramètres de génération PDF
    from app.services.bulletin_verification import generer_token_bulletin

    ecole = classe.ecole
    classe_nom = classe.nom
    annee_nom = annee.nom
    logo_path = os.path.join(current_app.static_folder, ecole.logo_path) if ecole and ecole.logo_path else None
    nom_ecole = ecole.nom if ecole else "École non renseignée"
    adresse_ecole = ecole.adresse if ecole else "-"
    contact_ecole = f"Tél: {ecole.telephone or '-'} - Email: {ecole.email or '-'}" if ecole else "-"
    devise_ecole = getattr(ecole, 'devise', '') or getattr(ecole, 'slogan', '')

    bulletins_pdf_payload = []
    for item in eleves_data:
        ins = item['inscription']
        eleve = item['eleve']
        data = item['data']

        token_bulletin = generer_token_bulletin(ecole_id, ins.id, periode_obj.id)
        verification_url = url_for('main.verifier_bulletin_public', token=token_bulletin, _external=True)

        bulletins_pdf_payload.append({
            'eleve': eleve,
            'notes_par_cours': data.get('notes_par_cours'),
            'moyennes_par_cours': data.get('moyennes_par_cours'),
            'moyenne_generale': data.get('moyenne_generale'),
            'logo_path': logo_path,
            'nom_ecole': nom_ecole,
            'adresse_ecole': adresse_ecole,
            'contact_ecole': contact_ecole,
            'classe_nom': classe_nom,
            'annee_scolaire_nom': annee_nom,
            'periode_nom': periode_nom,
            'rang': data.get('rang'),
            'rang_total': data.get('rang_total'),
            'appreciation_generale': data.get('appreciation'),
            'disciplines': data.get('disciplines'),
            'total_coefficients': data.get('total_coefficients'),
            'total_points': data.get('total_points'),
            'stats_classe': data.get('stats_classe'),
            'nb_absences': data.get('nb_absences'),
            'est_provisoire': data.get('est_provisoire', False),
            'verification_url': verification_url,
            'devise_ecole': devise_ecole,
        })

    try:
        buffer = generer_bulletins_classe_pdf(bulletins_pdf_payload)
        if not buffer:
            flash("Erreur lors de la génération du document PDF groupé.", "danger")
            return redirect(url_for('main.bulletins', classe_id=classe.id, periode_id=periode_obj.id))

        # En-tête et nom de fichier explicite : Bulletins_{NomClasse}_{NomPeriode}.pdf
        nom_classe_clean = re.sub(r'[^\w\-]', '_', classe.nom)
        nom_periode_clean = re.sub(r'[^\w\-]', '_', periode_nom)
        filename = f"Bulletins_{nom_classe_clean}_{nom_periode_clean}.pdf"

        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
    except Exception as e:
        current_app.logger.error(f"Erreur lors de la génération des bulletins groupés de la classe {classe.id} : {e}")
        flash("Erreur inattendue lors de la génération du PDF consolidé de la classe.", "danger")
        return redirect(url_for('main.bulletins', classe_id=classe.id, periode_id=periode_obj.id))


@main.route('/toggle_periode/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@tenant_required
def toggle_periode(id):
    ecole_id = g.ecole_id
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=ecole_id).first_or_404()

    # Bascule directe sans écran intermédiaire
    if not periode.publie:
        periode.publie = True
        periode.date_publication = datetime.utcnow()
        action_name = "BULLETIN_PUBLIE"
        desc = f"Publication officielle du bulletin {periode.nom}"
        niveau = "info"
        flash_msg = f"Période '{periode.nom}' clôturée et publiée avec succès. Les bulletins sont désormais officiels."
        flash_cat = "success"

        classes_ecole = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=periode.annee_id).all()
        for cl in classes_ecole:
            try:
                calculer_stats_et_classements_classe(
                    ecole_id=ecole_id,
                    classe_id=cl.id,
                    annee_id=periode.annee_id,
                    periode=periode.nom,
                    periode_publiee=True
                )
            except Exception as e:
                current_app.logger.warning(f"Calcul des rangs classe {cl.id} lors de la clôture : {e}")
    else:
        periode.publie = False
        action_name = "BULLETIN_REOUVERT"
        desc = f"Dépublication / réouverture administrative du bulletin {periode.nom}"
        niveau = "warning"
        flash_msg = f"Période '{periode.nom}' dépubliée. Les bulletins repassent en état provisoire."
        flash_cat = "warning"

    journal = JournalCorrection(
        action=action_name,
        description=desc,
        ecole_id=ecole_id,
        user_id=current_user.id,
        cible_type="periode_bulletin",
        cible_id=periode.id,
        niveau=niveau
    )
    db.session.add(journal)
    db.session.commit()

    flash(flash_msg, flash_cat)
    return redirect(request.referrer or url_for('main.gestion_periodes'))


@main.route('/reouvrir_periode/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@tenant_required
def reouvrir_periode(id):
    """Réouverture administrative explicite d'un bulletin / semestre."""
    ecole_id = g.ecole_id
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=ecole_id).first_or_404()
    periode.publie = False

    journal = JournalCorrection(
        action="BULLETIN_REOUVERT",
        description=f"Réouverture administrative explicite du bulletin {periode.nom}",
        ecole_id=ecole_id,
        user_id=current_user.id,
        cible_type="periode_bulletin",
        cible_id=periode.id,
        niveau="warning"
    )
    db.session.add(journal)
    db.session.commit()

    flash(f"Bulletin/Période {periode.nom} réouvert(e) avec succès. Les notes du semestre peuvent à présent être corrigées.", "warning")
    return redirect(url_for('main.gestion_periodes'))


@main.route('/periodes')
@login_required
@role_required('admin')
@tenant_required
def gestion_periodes():
    ecole_id = g.ecole_id
    annee = get_annee_consultee(ecole_id)
    query = get_ecole_filter_query(PeriodeBulletin)
    if annee:
        query = query.filter_by(annee_id=annee.id)
    periodes = query.all()
    return render_template("gestion_periodes.html", periodes=periodes, annee_consultee=annee)


@main.route('/activer_periode/<int:id>')
@login_required
@role_required('admin')
@tenant_required
def activer_periode(id):
    """Rendre une période active (période de travail) sans forcer sa publication officielle."""
    ecole_id = g.ecole_id
    annee = get_annee_consultee(ecole_id)
    filter_kwargs = {'ecole_id': ecole_id}
    if annee:
        filter_kwargs['annee_id'] = annee.id

    PeriodeBulletin.query.filter_by(**filter_kwargs).update({'periode_active': False})
    
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=ecole_id).first_or_404()
    periode.periode_active = True
    # IMPORTANT : Ne force PAS periode.publie = True (activation != publication)
    
    db.session.commit()
    flash(f"Période '{periode.nom}' définie comme période de travail active.", "success")
    return redirect(url_for('main.gestion_periodes'))


@main.route('/creer_periode', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@tenant_required
def creer_periode():
    """Créer une nouvelle période de bulletin"""
    ecole_id = g.ecole_id
    form = PeriodeForm()
    form.annee_id.choices = [(a.id, a.nom) for a in AnneeScolaire.query.filter_by(ecole_id=ecole_id).all()]
    
    if form.validate_on_submit():
        nom = form.nom.data
        annee_id = form.annee_id.data
        annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
        if not annee:
            flash("Année scolaire invalide pour cette école.", "danger")
            return redirect(url_for('main.creer_periode'))
        
        nouvelle_periode = PeriodeBulletin(
            nom=nom,
            annee_id=annee_id,
            ecole_id=ecole_id,
            publie=False,
            periode_active=False
        )
        db.session.add(nouvelle_periode)
        db.session.commit()
        
        flash(f"Période '{nom}' créée avec succès.", "success")
        return redirect(url_for('main.gestion_periodes'))
    
    return render_template('creer_periode.html', form=form)


@main.route('/verifier/bulletin/<token>')
def verifier_bulletin_public(token):
    """
    Page publique d'authentification et de vérification d'un bulletin scolaire (mobile-first).
    Ne requiert aucune authentification.
    N'expose aucune note, absence, paiement ou information privée parent.
    Exclue de l'indexation (noindex, nofollow, noarchive) et du cache (Cache-Control: no-store, private).
    """
    from datetime import datetime
    from flask import make_response
    from app.services.bulletin_verification import decoder_token_bulletin
    from app.services.evaluations import calculer_completude_inscription

    ecole_id, inscription_id, periode_id = decoder_token_bulletin(token)

    if not ecole_id or not inscription_id or not periode_id:
        resp = make_response(render_template(
            'verifier_bulletin.html',
            valide=False,
            message_erreur="Ce document est introuvable ou la signature de vérification n'est pas valide."
        ), 404)
        resp.headers["Cache-Control"] = "no-store, private, must-revalidate"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return resp

    inscription = Inscription.query.filter_by(id=inscription_id, ecole_id=ecole_id).first()
    periode = PeriodeBulletin.query.filter_by(id=periode_id, ecole_id=ecole_id).first()

    if not inscription or not periode or not inscription.eleve:
        resp = make_response(render_template(
            'verifier_bulletin.html',
            valide=False,
            message_erreur="Le bulletin correspondant à ce code est introuvable ou n'est plus actif."
        ), 404)
        resp.headers["Cache-Control"] = "no-store, private, must-revalidate"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return resp

    eleve = inscription.eleve
    classe = inscription.classe
    annee = inscription.annee_scolaire
    ecole = inscription.ecole or eleve.ecole

    # Évaluation de la complétude et du statut officiel vs provisoire en temps réel
    eval_info = calculer_completude_inscription(
        ecole_id,
        annee.id if annee else None,
        inscription,
        periode=periode.nom,
        periode_publiee=periode.publie,
    )

    is_official = bool(eval_info.get("is_official", False))
    statut_bulletin = "BULLETIN OFFICIEL" if is_official else "BULLETIN PROVISOIRE"
    statut_description = "Document authentique" if is_official else "Document authentique mais non définitif"
    matricule = eleve.code_parent or f"#{eleve.id}"

    resp = make_response(render_template(
        'verifier_bulletin.html',
        valide=True,
        is_official=is_official,
        statut_bulletin=statut_bulletin,
        statut_description=statut_description,
        eleve=eleve,
        matricule=matricule,
        classe=classe,
        annee=annee,
        ecole=ecole,
        periode=periode,
        date_verification=datetime.utcnow(),
    ), 200)

    resp.headers["Cache-Control"] = "no-store, private, must-revalidate"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return resp

