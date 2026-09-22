from collections import defaultdict
from flask import g, jsonify, request
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
from app.services import generer_bulletin_pdf
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

    # Récupérer la période active pour cette année
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

    periode_nom = periode_active.nom if periode_active else "Semestre 1"
    periode_publiee = bool(periode_active and periode_active.publie)

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

    # Récupération optimisée des notes liées à ces inscriptions
    notes_all = (
        Note.query.options(joinedload(Note.cours))
        .filter(Note.inscription_id.in_(inscr_ids), Note.ecole_id == ecole_id)
        .all()
    ) if inscr_ids else []

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

        if status == STATUS_NON_EVALUE:
            appreciation = 'Non évalué'
            appreciation_code = 'non-evalue'
            badge_class = 'badge-mention-non-evalue bg-secondary text-white'
        elif status == STATUS_PROVISOIRE:
            appreciation = 'En attente'
            appreciation_code = 'provisoire'
            badge_class = 'bg-light text-muted border'
        else:
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

        eleves_avec_moyennes.append({
            'inscription': ins,
            'eleve': eleve,
            'classe': ins.classe,
            'annee_scolaire': ins.annee_scolaire,
            'moyenne': moyenne if moyenne is not None else 0,
            'moyenne_raw': moyenne,
            'notes_count': eval_info['evaluated_subjects'],
            'eval_info': eval_info,
            'status': status,
            'appreciation': appreciation,
            'appreciation_code': appreciation_code,
            'badge_class': badge_class,
            'notes': student_notes,
            'bulletin_genere': has_bulletin,
            'bulletin_id': bulletins_existants[ins.id].id if has_bulletin else None,
            'rang_classe': None,
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

    # Calcul des rangs au sein de chaque classe annuelle via le service canonique
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

        complets_items = [it for it in c_items if it['status'] == STATUS_COMPLETE]
        provisoires_items = [it for it in c_items if it['status'] == STATUS_PROVISOIRE]
        non_evalues_items = [it for it in c_items if it['status'] == STATUS_NON_EVALUE]

        complets_items.sort(key=lambda x: x['moyenne_raw'] if x['moyenne_raw'] is not None else -1.0, reverse=True)
        provisoires_items.sort(key=lambda x: x['moyenne_raw'] if x['moyenne_raw'] is not None else -1.0, reverse=True)
        non_evalues_items.sort(key=lambda x: ((x['eleve'].nom or '').lower(), (x['eleve'].prenom or '').lower()))

        eleves_par_classe[c.id] = complets_items + provisoires_items + non_evalues_items

        classe_stats[c.id] = {
            'classe': c,
            'effectif': c_canon_stats['effectif_total'],
            'evalues_count': c_canon_stats['complets_count'] + c_canon_stats['provisoires_count'],
            'complets_count': c_canon_stats['complets_count'],
            'provisoires_count': c_canon_stats['provisoires_count'],
            'moyenne_classe': c_canon_stats['moyenne_classe_officielle'],
            'meilleure_moyenne': c_canon_stats['plus_forte_moyenne'],
            'pire_moyenne': c_canon_stats['plus_faible_moyenne'],
            'taux_reussite': c_canon_stats['taux_reussite']
        }

    # Statistiques globales de l'école (uniquement sur élèves complets et si période publiée)
    complets_globaux = [e for e in eleves_avec_moyennes if e['status'] == STATUS_COMPLETE and e['moyenne_raw'] is not None]
    if complets_globaux and periode_publiee:
        moyenne_generale = round(sum(e['moyenne_raw'] for e in complets_globaux) / len(complets_globaux), 2)
        meilleure_moyenne = max(e['moyenne_raw'] for e in complets_globaux)
        admis_g = sum(1 for e in complets_globaux if e['moyenne_raw'] >= 10)
        taux_reussite = round((admis_g / len(complets_globaux)) * 100, 1)
    else:
        moyenne_generale = None
        meilleure_moyenne = None
        taux_reussite = None

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': len(eleves_avec_moyennes),
            'eleves': [
                {
                    'eleve_id': it['eleve'].id,
                    'nom': f"{it['eleve'].prenom} {it['eleve'].nom}",
                    'classe_id': it['classe'].id if it['classe'] else None,
                    'classe_nom': it['classe'].nom if it['classe'] else '',
                    'moyenne': it['moyenne'],
                    'appreciation': it['appreciation'],
                    'appreciation_code': it['appreciation_code'],
                    'bulletin_genere': it['bulletin_genere'],
                    'notes_count': it['notes_count']
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
        eleves=eleves_avec_moyennes,
        moyenne_generale=moyenne_generale,
        meilleure_moyenne=meilleure_moyenne,
        taux_reussite=taux_reussite,
        total_eleves=len(inscriptions),
        bulletins_accessibles=bulletins_accessible_pour_parent() or (annee and annee.statut == 'archivee'),
        annee_consultee=annee,
        bulletins_modifiables=est_modifiable,
        statut_warning=statut_warning,
        periode_publiee=periode_publiee,
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


@main.route('/toggle_periode/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@tenant_required
def toggle_periode(id):
    ecole_id = g.ecole_id
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=ecole_id).first_or_404()

    # Si la période n'est pas encore publiée, l'administrateur demande sa publication officielle
    if not periode.publie:
        from app.services.evaluations import verifier_eligibilite_publication_periode
        verif = verifier_eligibilite_publication_periode(periode.ecole_id, periode.annee_id, periode.nom)
        
        confirme = (request.args.get('confirmer') == '1' or request.form.get('confirmer') == '1')

        # Si des élèves ont un bulletin incomplet et que l'administrateur n'a pas encore confirmé :
        if not verif["eligible"] and not confirme:
            return render_template(
                'confirmer_publication_periode.html',
                periode=periode,
                verif=verif
            )

        periode.publie = True
        periode.date_publication = datetime.utcnow()
        action_name = "BULLETIN_PUBLIE"
        desc = f"Publication officielle du bulletin {periode.nom}"
        niveau = "info"

        if not verif["eligible"]:
            flash_msg = (
                f"Période '{periode.nom}' publiée avec avertissement : "
                f"{verif['total_incomplets']} élève(s) ont un bulletin incomplet et restent en statut provisoire."
            )
            flash_cat = "warning"
        else:
            flash_msg = f"Période '{periode.nom}' publiée avec succès. Les bulletins complets sont désormais officiels."
            flash_cat = "success"
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
    return redirect(url_for('main.gestion_periodes'))


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

