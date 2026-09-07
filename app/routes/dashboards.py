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

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Nombre d'enfants par page, ajustable

    # Récupération paginée des enfants avec filtre école et relations chargées
    enfants_query = filtre_par_ecole(
        Eleve.query.options(
            db.joinedload(Eleve.classe),         # Classe de l'élève
            db.selectinload(Eleve.notes),        # Notes
            db.selectinload(Eleve.absences),     # Absences
            db.selectinload(Eleve.paiements)     # Paiements
        ).filter_by(parent_id=current_user.id),
        Eleve
    ).order_by(Eleve.nom, Eleve.prenom)

    enfants_pagination = enfants_query.paginate(page=page, per_page=per_page, error_out=False)
    enfants = enfants_pagination.items

    if not enfants:
        flash("Aucun élève n'est associé à votre compte parent", "warning")
        return render_template('parent_dashboard.html', enfants=[], pagination=enfants_pagination)

    # Calcul des statistiques pour chaque enfant
    for enfant in enfants:
        notes = enfant.notes
        absences = len(enfant.absences)
        paiements = len(enfant.paiements)

        total_pondere = sum(n.valeur * n.coefficient for n in notes)
        total_coefficients = sum(n.coefficient for n in notes)
        enfant.moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients > 0 else 0
        enfant.total_notes = len(notes)
        enfant.total_absences = absences
        enfant.total_paiements = paiements

    return render_template('parent_dashboard.html', enfants=enfants, pagination=enfants_pagination)


@main.route('/professeur/dashboard')
@login_required
@role_required('professeur')
def professeur_dashboard():
    """Tableau de bord professeur avec données personnalisées"""
    professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()

    if not professeur:
        flash("Profil professeur non trouvé. Contactez l'administrateur.", "warning")
        return redirect(url_for('main.logout'))

    mes_cours = Cours.query.filter_by(professeur_id=professeur.id).all()

    emplois = EmploiTemps.query.filter_by(professeur_id=professeur.id).order_by(
        EmploiTemps.jour, EmploiTemps.heure_debut
    ).all()

    stats = {
        'total_eleves': len(set([note.eleve_id for cours in mes_cours for note in cours.notes])),
        'total_cours': len(mes_cours),
        'moyenne_generale': db.session.query(func.avg(Note.valeur)).filter(
            Note.cours_id.in_([c.id for c in mes_cours])
        ).scalar() or 0
    }

    cours_ids = [c.id for c in mes_cours]
    dernieres_notes = Note.query.filter(Note.cours_id.in_(cours_ids)).order_by(Note.date_evaluation.desc()).limit(5).all()

    now = datetime.now()
    aujourdhui = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    return render_template(
        'professeur_dashboard.html',
        stats=stats,
        mes_cours=mes_cours,
        dernieres_notes=dernieres_notes,
        emplois=emplois,
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

    emplois = EmploiTemps.query.filter_by(professeur_id=professeur.id).order_by(
        EmploiTemps.jour, EmploiTemps.heure_debut
    ).all()

    return render_template('professeur_home.html', emplois=emplois)


@main.route('/onboarding', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def onboarding():
    """Parcours d'onboarding dédié pour l'administrateur d'établissement avant tout accès au dashboard"""
    from flask import g, jsonify
    from app.utils import get_school_setup_state
    from app.models import Ecole, AnneeScolaire, Classe

    ecole = current_user.ecole
    if not ecole:
        flash("Votre compte administrateur n'est rattaché à aucun établissement.", "danger")
        return redirect(url_for('main.logout'))

    setup_state = get_school_setup_state(ecole.id)
    step = setup_state['current_step']  # 'year', 'class', or 'complete'
    active_year = setup_state.get('active_year')

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

                if dt_fin <= dt_debut:
                    flash("La date de fin doit être postérieure à la date de début.", "warning")
                    return redirect(url_for('main.onboarding'))

                # Vérifier si une année avec ce nom existe déjà pour cette école
                existante = AnneeScolaire.query.filter_by(nom=nom, ecole_id=ecole.id).first()
                if existante:
                    AnneeScolaire.query.filter_by(ecole_id=ecole.id).update({'statut': 'archivee'})
                    existante.statut = 'active'
                    existante.date_debut = dt_debut
                    existante.date_fin = dt_fin
                else:
                    AnneeScolaire.query.filter_by(ecole_id=ecole.id).update({'statut': 'archivee'})
                    nouvelle_annee = AnneeScolaire(
                        nom=nom,
                        date_debut=dt_debut,
                        date_fin=dt_fin,
                        statut='active',
                        ecole_id=ecole.id
                    )
                    db.session.add(nouvelle_annee)

                db.session.commit()
                if hasattr(g, '_school_setup_cache'):
                    g._school_setup_cache.pop(ecole.id, None)

                flash(f"Année scolaire « {nom} » configurée et activée avec succès 🎉", "success")
                return redirect(url_for('main.onboarding'))
            except Exception as e:
                db.session.rollback()
                flash(f"Erreur lors de la configuration de l'année : {str(e)}", "danger")
                return redirect(url_for('main.onboarding'))

        # Étape 2 : Création de la première classe
        elif action == 'creer_classe':
            if not active_year:
                flash("Veuillez d'abord configurer une année scolaire active.", "warning")
                return redirect(url_for('main.onboarding'))

            nom_classe = request.form.get('nom', '').strip()
            niveau = request.form.get('niveau', '').strip()
            salle = request.form.get('salle', '').strip()
            try:
                capacite = int(request.form.get('capacite') or 35)
            except (ValueError, TypeError):
                capacite = 35

            if not nom_classe or not niveau:
                flash("Le nom et le niveau de la classe sont obligatoires.", "danger")
                return redirect(url_for('main.onboarding'))

            existing_classe = Classe.query.filter_by(
                nom=nom_classe,
                annee_scolaire_id=active_year.id,
                ecole_id=ecole.id
            ).first()
            if existing_classe:
                flash("Une classe avec ce nom existe déjà pour cette année scolaire.", "warning")
                return redirect(url_for('main.onboarding'))

            try:
                nouvelle_classe = Classe(
                    nom=nom_classe,
                    niveau=niveau,
                    salle=salle or None,
                    capacite=capacite,
                    effectif=0,
                    annee_scolaire_id=active_year.id,
                    ecole_id=ecole.id
                )
                db.session.add(nouvelle_classe)
                db.session.commit()
                if hasattr(g, '_school_setup_cache'):
                    g._school_setup_cache.pop(ecole.id, None)

                flash(f"Première classe « {nom_classe} » créée avec succès 🎉", "success")
                return redirect(url_for('main.onboarding'))
            except Exception as e:
                db.session.rollback()
                flash(f"Erreur lors de la création de la classe : {str(e)}", "danger")
                return redirect(url_for('main.onboarding'))

    return render_template(
        'onboarding.html',
        ecole=ecole,
        setup_state=setup_state,
        step=step,
        active_year=active_year
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

