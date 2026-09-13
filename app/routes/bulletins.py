from collections import defaultdict
from flask import request
from . import main
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
    func,
    get_ecole_filter_query,
    joinedload,
    login_required,
    redirect,
    render_template,
    role_required,
    send_file,
    url_for,
)
from app.models import Bulletin, Inscription
from app.services import generer_bulletin_pdf
from app.services.annees_scolaires import get_annee_consultee
from app.services.bulletins_annuels import (
    statut_annee_bulletins,
    bulletins_modifiables,
    get_inscriptions_bulletins,
    calculer_bulletin_data,
    generer_ou_recuperer_bulletin,
    modifier_appreciation_bulletin,
    supprimer_bulletin,
    MESSAGE_ANNEE_PLANIFIEE,
    MESSAGE_ANNEE_ARCHIVEE,
)


@main.route('/bulletin_eleve/<int:id>')
@main.route('/bulletin/<int:id>')
@main.route('/bulletins/<int:id>')
@main.route('/bulletin/inscription/<int:inscription_id>')
@login_required
@role_required('admin', 'professeur', 'parent')
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
    ecole_id = current_user.ecole_id
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
        periode_demandee = periode_active.nom if periode_active else "Trimestre 1"

    # Calcul des données du bulletin strictement depuis Inscription et ses Notes
    data, err = calculer_bulletin_data(ecole_id, annee, inscription, periode=periode_demandee)
    if err:
        flash(f"Erreur lors du calcul du bulletin : {err}", "danger")
        return redirect(url_for('main.bulletins'))

    ecole = eleve.ecole
    classe_nom = inscription.classe.nom if inscription.classe else "Sans classe"
    annee_nom = inscription.annee_scolaire.nom if inscription.annee_scolaire else annee.nom

    try:
        buffer = generer_bulletin_pdf(
            eleve=eleve,
            notes_par_cours=data['notes_par_cours'],
            moyennes_par_cours=data['moyennes_par_cours'],
            moyenne_generale=data['moyenne_generale'],
            logo_path=ecole.logo_path if ecole and ecole.logo_path else None,
            nom_ecole=ecole.nom if ecole else "École non renseignée",
            adresse_ecole=ecole.adresse if ecole else "-",
            contact_ecole=f"Tél: {ecole.telephone or '-'} - Email: {ecole.email or '-'}" if ecole else "-",
            classe_nom=classe_nom,
            annee_scolaire_nom=annee_nom,
            periode_nom=periode_demandee,
            rang=data['rang'],
            rang_total=data['rang_total'],
            appreciation_generale=data['appreciation'],
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
def bulletins():
    """
    Page d'accueil des bulletins organisés par classe et année scolaire consultée.
    Règle 2C-5D : Consomme get_annee_consultee(ecole_id) sans modifier la session.
    """
    ecole_id = current_user.ecole_id
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
    classes = Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee.id
    ).order_by(Classe.nom.asc()).all()

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

    # Récupération optimisée des notes liées à ces inscriptions
    notes_all = (
        Note.query.options(joinedload(Note.cours))
        .filter(Note.inscription_id.in_(inscr_ids), Note.ecole_id == ecole_id)
        .all()
    ) if inscr_ids else []

    notes_par_inscription = defaultdict(list)
    for n in notes_all:
        notes_par_inscription[n.inscription_id].append(n)

    # Calcul des moyennes et mentions individuelles basées sur Inscription
    eleves_avec_moyennes = []
    for ins in inscriptions:
        eleve = ins.eleve
        student_notes = notes_par_inscription.get(ins.id, [])
        if student_notes:
            total_pondere = sum((n.valeur or 0) * (n.coefficient or 1) for n in student_notes)
            total_coefficients = sum((n.coefficient or 1) for n in student_notes)
            moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients > 0 else 0
        else:
            moyenne = 0

        # Mention et badge
        if len(student_notes) > 0:
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

        eleves_avec_moyennes.append({
            'inscription': ins,
            'eleve': eleve,
            'classe': ins.classe,
            'annee_scolaire': ins.annee_scolaire,
            'moyenne': moyenne,
            'notes_count': len(student_notes),
            'appreciation': appreciation,
            'appreciation_code': appreciation_code,
            'badge_class': badge_class,
            'notes': student_notes,
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

    # Calcul des rangs au sein de chaque classe annuelle
    classe_stats = {}
    for c in classes:
        c_items = eleves_par_classe.get(c.id, [])
        c_evalues = [it for it in c_items if it['notes_count'] > 0]
        c_non_evalues = [it for it in c_items if it['notes_count'] == 0]

        # Tri : évalués par moyenne décroissante, puis non-évalués par nom
        c_evalues.sort(key=lambda x: x['moyenne'], reverse=True)
        c_non_evalues.sort(key=lambda x: ((x['eleve'].nom or '').lower(), (x['eleve'].prenom or '').lower()))

        for rank, it in enumerate(c_evalues, 1):
            it['rang_classe'] = rank
            it['rang_classe_total'] = len(c_evalues)
        for it in c_non_evalues:
            it['rang_classe'] = None
            it['rang_classe_total'] = len(c_evalues)

        eleves_par_classe[c.id] = c_evalues + c_non_evalues

        evalues_count = len(c_evalues)
        effectif = len(c_items)
        moyenne_classe = round(sum(it['moyenne'] for it in c_evalues) / evalues_count, 2) if evalues_count > 0 else 0
        meilleure_moyenne = max((it['moyenne'] for it in c_evalues), default=0)
        pire_moyenne = min((it['moyenne'] for it in c_evalues), default=0)
        admis_count = sum(1 for it in c_evalues if it['moyenne'] >= 10)
        taux_reussite = round((admis_count / evalues_count) * 100, 1) if evalues_count > 0 else 0

        classe_stats[c.id] = {
            'classe': c,
            'effectif': effectif,
            'evalues_count': evalues_count,
            'moyenne_classe': moyenne_classe,
            'meilleure_moyenne': meilleure_moyenne,
            'pire_moyenne': pire_moyenne,
            'admis_count': admis_count,
            'taux_reussite': taux_reussite
        }

    # Statistiques globales de l'école
    evalues_globaux = [e for e in eleves_avec_moyennes if e['notes_count'] > 0]
    if evalues_globaux:
        moyenne_generale = round(sum(e['moyenne'] for e in evalues_globaux) / len(evalues_globaux), 2)
        meilleure_moyenne = max(e['moyenne'] for e in evalues_globaux)
        taux_reussite = round(sum(1 for e in evalues_globaux if e['moyenne'] >= 10) / len(evalues_globaux) * 100, 1)
    else:
        moyenne_generale = 0
        meilleure_moyenne = 0
        taux_reussite = 0

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
        statut_warning=statut_warning
    )


@main.route('/bulletin/<int:id>/supprimer', methods=['POST'])
@main.route('/bulletins/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def route_supprimer_bulletin(id):
    """Supprime un bulletin persistant (interdit sur année archivée ou planifiée)."""
    annee = get_annee_consultee(current_user.ecole_id)
    succes, err = supprimer_bulletin(current_user.ecole_id, annee, current_user, id)
    if not succes:
        flash(err or "Impossible de supprimer ce bulletin.", "danger")
    else:
        flash("Bulletin supprimé avec succès.", "success")
    return redirect(url_for('main.bulletins'))


@main.route('/bulletin/<int:id>/appreciation', methods=['POST'])
@login_required
@role_required('admin', 'professeur')
def route_modifier_appreciation(id):
    """Modifie l'appréciation générale d'un bulletin (interdit sur année archivée)."""
    annee = get_annee_consultee(current_user.ecole_id)
    nouvelle_appreciation = request.form.get('appreciation', '')
    bulletin_mod, err = modifier_appreciation_bulletin(current_user.ecole_id, annee, current_user, id, nouvelle_appreciation)
    if err:
        flash(err, "danger")
    else:
        flash("Appréciation mise à jour avec succès.", "success")
    return redirect(url_for('main.bulletins'))


@main.route('/toggle_periode/<int:id>')
@login_required
@role_required('admin')
def toggle_periode(id):
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()
    periode.publie = not periode.publie
    if periode.publie:
        periode.date_publication = datetime.utcnow()
    db.session.commit()
    flash(f"Période {periode.nom} {'activée' if periode.publie else 'désactivée'} avec succès.", "success")
    return redirect(url_for('main.gestion_periodes'))


@main.route('/periodes')
@login_required
@role_required('admin')
def gestion_periodes():
    annee = get_annee_consultee(current_user.ecole_id)
    query = get_ecole_filter_query(PeriodeBulletin)
    if annee:
        query = query.filter_by(annee_id=annee.id)
    periodes = query.all()
    return render_template("gestion_periodes.html", periodes=periodes, annee_consultee=annee)


@main.route('/activer_periode/<int:id>')
@login_required
@role_required('admin')
def activer_periode(id):
    """Rendre une période active (une seule période active à la fois)"""
    annee = get_annee_consultee(current_user.ecole_id)
    filter_kwargs = {'ecole_id': current_user.ecole_id}
    if annee:
        filter_kwargs['annee_id'] = annee.id

    PeriodeBulletin.query.filter_by(**filter_kwargs).update({'periode_active': False})
    
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()
    periode.periode_active = True
    periode.publie = True
    periode.date_publication = datetime.utcnow()
    
    db.session.commit()
    flash(f"Période {periode.nom} activée avec succès. Les parents peuvent maintenant accéder aux bulletins.", "success")
    return redirect(url_for('main.gestion_periodes'))


@main.route('/creer_periode', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def creer_periode():
    """Créer une nouvelle période de bulletin"""
    form = PeriodeForm()
    form.annee_id.choices = [(a.id, a.nom) for a in AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).all()]
    
    if form.validate_on_submit():
        nom = form.nom.data
        annee_id = form.annee_id.data
        annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=current_user.ecole_id).first()
        if not annee:
            flash("Année scolaire invalide pour cette école.", "danger")
            return redirect(url_for('main.creer_periode'))
        
        nouvelle_periode = PeriodeBulletin(
            nom=nom,
            annee_id=annee_id,
            ecole_id=current_user.ecole_id,
            publie=False,
            periode_active=False
        )
        db.session.add(nouvelle_periode)
        db.session.commit()
        
        flash(f"Période '{nom}' créée avec succès.", "success")
        return redirect(url_for('main.gestion_periodes'))
    
    return render_template('creer_periode.html', form=form)
