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
