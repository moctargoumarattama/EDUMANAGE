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
    """Vue unique et simplifiée pour l'espace parent"""
    from datetime import datetime
    from collections import defaultdict
    from sqlalchemy.orm import selectinload, joinedload
    from app.models import Inscription, Eleve, Note, Absence, Paiement
    from app.services.annees_scolaires import get_annee_consultee
    from app.services.paiements_annuels import get_finances_inscription

    ecole_id = getattr(current_user, "ecole_id", None)
    annee_consultee = get_annee_consultee(ecole_id) if ecole_id else None

    if not ecole_id or not annee_consultee:
        return render_template(
            'parent_dashboard.html',
            inscriptions=[],
            inscription_selectionnee=None,
            enfant_selectionne=None,
            annee_consultee=annee_consultee
        )

    # Récupérer strictly les inscriptions des enfants du parent connecté
    inscriptions = (
        Inscription.query
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .options(
            joinedload(Inscription.eleve),
            joinedload(Inscription.classe),
            selectinload(Inscription.notes).joinedload(Note.cours),
            selectinload(Inscription.absences).joinedload(Absence.cours),
            selectinload(Inscription.paiements)
        )
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_consultee.id,
            Eleve.ecole_id == ecole_id,
            Eleve.parent_id == current_user.id
        )
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )

    if not inscriptions:
        return render_template(
            'parent_dashboard.html',
            inscriptions=[],
            inscription_selectionnee=None,
            enfant_selectionne=None,
            annee_consultee=annee_consultee
        )

    # Sélection de l'enfant
    requested_id = request.args.get('enfant_id', type=int)
    inscription_selectionnee = None
    if requested_id:
        inscription_selectionnee = next((ins for ins in inscriptions if ins.eleve_id == requested_id), None)

    if not inscription_selectionnee:
        inscription_selectionnee = inscriptions[0]

    enfant_selectionne = inscription_selectionnee.eleve

    # 1. Notes par matière
    notes = inscription_selectionnee.notes or []
    notes_triees = sorted(notes, key=lambda n: n.date_evaluation or datetime.min, reverse=True)
    notes_par_matiere = defaultdict(list)
    for n in notes_triees:
        nom_matiere = n.cours.nom if n.cours else "Matière"
        notes_par_matiere[nom_matiere].append(n)

    # 2. Absences
    absences = inscription_selectionnee.absences or []
    absences_triees = sorted(absences, key=lambda a: a.date_absence or datetime.min, reverse=True)
    stats_absences = {
        'total': len(absences),
        'justifiees': sum(1 for a in absences if a.justifiee),
        'non_justifiees': sum(1 for a in absences if not a.justifiee)
    }

    # 3. Paiements
    finances = get_finances_inscription(inscription_selectionnee)
    paiements = inscription_selectionnee.paiements or []
    paiements_triees = sorted(paiements, key=lambda p: p.date_paiement or datetime.min, reverse=True)

    return render_template(
        'parent_dashboard.html',
        inscriptions=inscriptions,
        inscription_selectionnee=inscription_selectionnee,
        enfant_selectionne=enfant_selectionne,
        notes_par_matiere=dict(notes_par_matiere),
        absences=absences_triees,
        stats_absences=stats_absences,
        finances=finances,
        paiements=paiements_triees,
        annee_consultee=annee_consultee
    )



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
    jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    jour_actuel = jours[now.weekday()]
    cours_aujourdhui = sorted(
        [e for e in emplois if getattr(e, "jour", None) == jour_actuel],
        key=lambda e: e.heure_debut
    )
    prochain_cours = next((e for e in cours_aujourdhui if e.heure_fin and e.heure_fin >= now.time()), None)
    classe_ids = {c.classe_id for c in mes_cours if getattr(c, "classe_id", None)}
    appel_cible = (
        prochain_cours
        or next((e for e in cours_aujourdhui if getattr(e, "classe_id", None) and getattr(e, "cours_id", None)), None)
        or next((e for e in emplois if getattr(e, "classe_id", None) and getattr(e, "cours_id", None)), None)
    )
    if not appel_cible:
        appel_cible = next((c for c in mes_cours if getattr(c, "classe_id", None) and getattr(c, "id", None)), None)
    appel_classe_id = getattr(appel_cible, "classe_id", None)
    appel_cours_id = getattr(appel_cible, "cours_id", None) or getattr(appel_cible, "id", None)

    return render_template(
        'professeur_dashboard.html',
        stats=stats,
        mes_cours=mes_cours,
        dernieres_notes=dernieres_notes,
        emplois=emplois,
        cours_aujourdhui=cours_aujourdhui,
        prochain_cours=prochain_cours,
        appel_cible=appel_cible,
        appel_classe_id=appel_classe_id,
        appel_cours_id=appel_cours_id,
        total_classes=len(classe_ids),
        annee_consultee=annee_consultee,
        now=now,
        aujourdhui=jours
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
            from app.services.annees_scolaires import construire_nom_annee, valider_dates_annee

            annee_court = request.form.get('annee_debut_court', '').strip()
            date_debut_str = request.form.get('date_debut', '').strip()
            date_fin_str = request.form.get('date_fin', '').strip()

            nom, debut_annee, fin_annee, err_nom = construire_nom_annee(annee_court)
            if err_nom:
                flash(err_nom, "danger")
                return redirect(url_for('main.onboarding'))

            if not date_debut_str or not date_fin_str:
                flash("Veuillez renseigner tous les champs obligatoires de l'année scolaire.", "danger")
                return redirect(url_for('main.onboarding'))

            try:
                dt_debut = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
                dt_fin = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                flash("Format de date invalide (AAAA-MM-JJ).", "danger")
                return redirect(url_for('main.onboarding'))

            ok_dates, err_dates = valider_dates_annee(dt_debut, dt_fin, debut_annee, fin_annee)
            if not ok_dates:
                flash(err_dates, "danger")
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
