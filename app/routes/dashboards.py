from . import main
from .common import (
    Absence,
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Eleve,
    EmploiTemps,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
    current_user,
    date,
    datetime,
    db,
    filtre_par_ecole,
    flash,
    func,
    get_ecole_filter_query,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    timedelta,
    url_for,
)


@main.route('/dashboard')
@login_required
def dashboard():
    """Redirection vers le tableau de bord approprié selon le rôle"""
    role = getattr(current_user, "role", None)
    endpoint_par_role = {
        "super_admin": "main.index",
        "admin": "main.index",
        "professeur": "main.professeur_dashboard",
        "parent": "main.parent_dashboard",
    }
    return redirect(url_for(endpoint_par_role.get(role, "main.index")))


@main.route('/parent/dashboard')
@login_required
@role_required('parent')
def parent_dashboard():
    """Tableau de bord parent avec pagination pour enfants et notes"""
    from app.services.annees_scolaires import get_annee_consultee
    from app.services.statistiques_annuelles import (
        enrichir_enfants_parent_annuel,
        get_parent_enfants_query,
    )

    page = request.args.get('page', 1, type=int)
    per_page = 10
    annee_consultee = get_annee_consultee(current_user.ecole_id)
    enfants_query = get_parent_enfants_query(current_user.ecole_id, annee_consultee, current_user.id) if annee_consultee else None
    enfants_pagination = enfants_query.paginate(page=page, per_page=per_page, error_out=False) if enfants_query else None
    enfants = enrichir_enfants_parent_annuel(enfants_pagination.items) if enfants_pagination else []

    if not enfants:
        flash("Aucun Ã©lÃ¨ve n'est associÃ© Ã  votre compte parent", "warning")
        return render_template('parent_dashboard.html', enfants=[], pagination=enfants_pagination, annee_consultee=annee_consultee)

    return render_template('parent_dashboard.html', enfants=enfants, pagination=enfants_pagination, annee_consultee=annee_consultee)



@main.route('/professeur/dashboard')
@login_required
@role_required('professeur')
def professeur_dashboard():
    from app.services.annees_scolaires import get_annee_consultee
    from app.services.statistiques_annuelles import get_professeur_dashboard_annuel
    """Tableau de bord professeur avec données personnalisées"""
    professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()

    if not professeur:
        flash("Profil professeur non trouvé. Contactez l'administrateur.", "warning")
        return redirect(url_for('main.logout'))

    from app.services.emploi_temps_annuel import get_creneaux_annee
    annee_consultee = get_annee_consultee(current_user.ecole_id)
    donnees_professeur = get_professeur_dashboard_annuel(current_user.ecole_id, annee_consultee, professeur.id)
    mes_cours = donnees_professeur["mes_cours"]
    stats = donnees_professeur["stats"]
    dernieres_notes = donnees_professeur["dernieres_notes"]
    emplois = get_creneaux_annee(current_user.ecole_id, annee_consultee, professeur_id=professeur.id) if annee_consultee else []

    now = datetime.now()
    aujourdhui = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    return render_template(
        'professeur_dashboard.html',
        stats=stats,
        mes_cours=mes_cours,
        dernieres_notes=dernieres_notes,
        emplois=emplois,
        annee_consultee=annee_consultee,
        now=now,
        aujourdhui=aujourdhui
    )


@main.route('/professeur')
@login_required
@role_required('professeur')
def professeur_home():
    """Page d'accueil du professeur avec emploi du temps"""
    professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()

    if not professeur:
        flash("Profil professeur non trouvé", "danger")
        return redirect(url_for('main.logout'))

    from app.utils import get_annee_consultee
    from app.services.emploi_temps_annuel import get_creneaux_annee
    annee_consultee = get_annee_consultee(current_user.ecole_id)
    emplois = get_creneaux_annee(current_user.ecole_id, annee_consultee, professeur_id=professeur.id) if annee_consultee else []

    return render_template('professeur_home.html', emplois=emplois)


@main.route('/onboarding', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def onboarding():
    """Parcours d'onboarding dédié pour l'administrateur d'établissement avant tout accès au dashboard"""
    from flask import g, jsonify, session
    from app.utils import (
        get_school_setup_state,
        creer_ou_activer_annee_scolaire,
    )
    from app.services.structure_annuelle import (
        sauvegarder_structure_annee,
        get_niveaux_catalogue_grouped_for_onboarding,
    )

    ecole = current_user.ecole
    if not ecole:
        flash("Votre compte administrateur n'est rattaché à aucun établissement.", "danger")
        return redirect(url_for('main.logout'))

    setup_state = get_school_setup_state(ecole.id, force_refresh=True)
    active_year = setup_state.get('active_year')

    # Contrôle strict du cycle de vie des étapes (impossible de forcer 'complete' si setup incomplet)
    if not setup_state['setup_complete']:
        step = setup_state['current_step']  # Strictement 'year' ou 'class'
    else:
        # Configuration complète en base
        just_completed = session.pop('onboarding_just_completed', False) or request.args.get('step') == 'complete'
        if just_completed:
            step = 'complete'
        else:
            return redirect(url_for('main.index'))

    # Traitement des formulaires au sein de l'expérience d'onboarding
    if request.method == 'POST':
        action = request.form.get('action')

        # Étape 1 : Création / activation de l'année scolaire
        if action == 'creer_annee':
            nom = request.form.get('nom', '').strip()
            date_debut_str = request.form.get('date_debut', '').strip()
            date_fin_str = request.form.get('date_fin', '').strip()

            if not nom or not date_debut_str or not date_fin_str:
                flash("Veuillez renseigner tous les champs obligatoires de l'année scolaire.", "danger")
                return redirect(url_for('main.onboarding'))

            try:
                dt_debut = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
                dt_fin = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                flash("Format de date invalide (AAAA-MM-JJ).", "danger")
                return redirect(url_for('main.onboarding'))

            annee, error_msg = creer_ou_activer_annee_scolaire(ecole.id, nom, dt_debut, dt_fin)
            if error_msg:
                flash(error_msg, "danger")
                return redirect(url_for('main.onboarding'))

            flash(f"Année scolaire « {annee.nom} » configurée et activée avec succès 🎉", "success")
            return redirect(url_for('main.onboarding'))

        # Étape 2 : Création de la première classe
        elif action == 'configurer_pedagogie':
            if not active_year:
                flash("Veuillez d'abord configurer une année scolaire active.", "warning")
                return redirect(url_for('main.onboarding'))

            niveau_ids = request.form.getlist('niveau_ids')
            if not niveau_ids:
                flash("Veuillez sélectionner au moins un niveau scolaire.", "danger")
                return redirect(url_for('main.onboarding'))

            res, error_msg = sauvegarder_structure_annee(
                ecole_id=ecole.id,
                annee_scolaire_id=active_year.id,
                niveau_ids=niveau_ids
            )
            if error_msg:
                flash(error_msg, "danger")
                return redirect(url_for('main.onboarding'))

            db.session.commit()
            session['onboarding_just_completed'] = True
            flash("Configuration pedagogique enregistree avec succes.", "success")
            return redirect(url_for('main.onboarding', step='complete'))

    niveau_configs_grouped = get_niveaux_catalogue_grouped_for_onboarding(ecole.id, active_year.id if active_year else None)

    return render_template(
        'onboarding.html',
        ecole=ecole,
        setup_state=setup_state,
        step=step,
        active_year=active_year,
        niveau_configs_grouped=niveau_configs_grouped
    )


@main.route('/api/admin/tour/complete', methods=['POST'])
@login_required
@role_required('admin')
def api_admin_tour_complete():
    """Enregistre la complétion ou le passage explicite de la visite guidée pour l'administrateur courant"""
    from flask import jsonify
    from app.models import ADMIN_TOUR_VERSION
    current_user.admin_tour_version = ADMIN_TOUR_VERSION
    db.session.commit()
    return jsonify({
        'success': True,
        'admin_tour_version': current_user.admin_tour_version,
        'message': 'Visite guidée marquée comme complétée.'
    })

