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
        "admin": "main.index",
        "enseignant": "main.enseignant_dashboard",
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

@main.route('/statistiques/avancees')
@login_required
@role_required('admin')
def statistiques_avancees():
    # -------------------------
    # Gestion des paramètres de période
    # -------------------------
    periode = request.args.get('periode', 'month')
    date_debut = request.args.get('date_debut')
    date_fin = request.args.get('date_fin')

    try:
        if date_debut:
            date_debut = datetime.strptime(date_debut, '%Y-%m-%d')
        if date_fin:
            date_fin = datetime.strptime(date_fin, '%Y-%m-%d')
    except ValueError:
        flash("Format de date invalide. Utilisation de la période par défaut.", "warning")
        date_debut = date_fin = None

    if not date_debut or not date_fin:
        now = datetime.now()
        if periode == 'today':
            date_debut = now.replace(hour=0, minute=0, second=0, microsecond=0)
            date_fin = now
        elif periode == 'week':
            date_debut = now - timedelta(days=7)
            date_fin = now
        elif periode == 'month':
            date_debut = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            date_fin = now
        elif periode == 'year':
            date_debut = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            date_fin = now
        else:  # all
            date_debut = datetime.min
            date_fin = now

    # -------------------------
    # Statistiques globales (multi-école)
    # -------------------------
    stats = {
        'eleves': get_ecole_filter_query(Eleve).count(),
        'enseignants': get_ecole_filter_query(Utilisateur).filter_by(role='professeur').count(),
        'cours': get_ecole_filter_query(Cours).count(),
        'absences_periode': get_ecole_filter_query(Absence).filter(
            Absence.date_absence.between(date_debut, date_fin)
        ).count(),
        'revenu_periode': get_ecole_filter_query(Paiement).with_entities(
            func.coalesce(func.sum(Paiement.montant), 0)
        ).filter(Paiement.date_paiement.between(date_debut, date_fin)).scalar(),
        'paiements': get_ecole_filter_query(Paiement).filter(
            Paiement.date_paiement.between(date_debut, date_fin)
        ).count(),
        'notes': get_ecole_filter_query(Note).filter(
            Note.date_evaluation.between(date_debut, date_fin)
        ).count(),
        'nouveaux_eleves': get_ecole_filter_query(Eleve).filter(
            Eleve.date_inscription.between(date_debut, date_fin)
        ).count()
    }

    # -------------------------
    # Moyenne générale
    # -------------------------
    moyenne_generale = get_ecole_filter_query(Note).with_entities(
        func.coalesce(func.sum(Note.valeur * Note.coefficient) / func.sum(Note.coefficient), 0)
    ).scalar()
    stats['moyenne_generale'] = round(moyenne_generale, 2)

    # -------------------------
    # Derniers élèves inscrits
    # -------------------------
    recent_eleves = get_ecole_filter_query(Eleve).order_by(Eleve.date_inscription.desc()).limit(5).all()

    # -------------------------
    # Absences par élève (top 10)
    # -------------------------
    absences_top = db.session.query(
        Eleve.prenom, Eleve.nom, func.count(Absence.id)
    ).join(Eleve.absences).filter(
        Absence.date_absence.between(date_debut, date_fin)
    ).group_by(Eleve.id).order_by(func.count(Absence.id).desc()).limit(10).all()

    noms_eleves = [f"{e[0]} {e[1]}" for e in absences_top]
    absences_par_eleve = [e[2] for e in absences_top]

    # -------------------------
    # Répartition des notes
    # -------------------------
    repartition_notes = [0, 0, 0, 0]  # <5 | 5-9.9 | 10-14.9 | 15+
    for note_val, _ in get_ecole_filter_query(Note).with_entities(Note.valeur, Note.id).all():
        if note_val < 5:
            repartition_notes[0] += 1
        elif note_val < 10:
            repartition_notes[1] += 1
        elif note_val < 15:
            repartition_notes[2] += 1
        else:
            repartition_notes[3] += 1

    # -------------------------
    # Top classes par moyenne
    # -------------------------
    classes_data = []
    classes_labels = []
    classes = get_ecole_filter_query(Classe).join(Classe.eleves).distinct().all()

    for classe in classes:
        eleves_ids = [e.id for e in classe.eleves]
        if eleves_ids:
            notes_query = get_ecole_filter_query(Note).filter(Note.eleve_id.in_(eleves_ids))
            total = notes_query.with_entities(func.coalesce(func.sum(Note.valeur * Note.coefficient), 0)).scalar()
            coeff = notes_query.with_entities(func.coalesce(func.sum(Note.coefficient), 0)).scalar()
            moyenne_classe = round(total / coeff, 2) if coeff > 0 else 0
            classes_labels.append(classe.nom)
            classes_data.append(moyenne_classe)

    # Trier et limiter au top 5
    if len(classes_data) > 5:
        combined = sorted(zip(classes_labels, classes_data), key=lambda x: x[1], reverse=True)[:5]
        classes_labels, classes_data = zip(*combined) if combined else ([], [])

    # -------------------------
    # Activités récentes (absences, paiements, notes)
    # -------------------------
    activites_recentes = []

    absences_recentes = get_ecole_filter_query(Absence).order_by(Absence.date_absence.desc()).limit(3).all()
    paiements_recent = get_ecole_filter_query(Paiement).order_by(Paiement.date_paiement.desc()).limit(3).all()
    notes_recentes = get_ecole_filter_query(Note).order_by(Note.date_evaluation.desc()).limit(3).all()

    for a in absences_recentes:
        activites_recentes.append({
            'type': 'absence',
            'details': f"{a.eleve.prenom} {a.eleve.nom} - {a.motif or 'Non spécifié'}",
            'date': datetime.combine(a.date_absence, datetime.min.time()) if isinstance(a.date_absence, date) else a.date_absence,
            'statut': 'completed'
        })
    for p in paiements_recent:
        activites_recentes.append({
            'type': 'paiement',
            'details': f"{p.eleve.prenom} {p.eleve.nom} - {p.montant} FCFA",
            'date': p.date_paiement,
            'statut': 'completed'
        })
    for n in notes_recentes:
        activites_recentes.append({
            'type': 'note',
            'details': f"{n.eleve.prenom} {n.eleve.nom} - {n.valeur}/20 en {n.cours.nom}",
            'date': n.date_evaluation,
            'statut': 'completed'
        })

    # Trier par date
    for act in activites_recentes:
        if isinstance(act['date'], date) and not isinstance(act['date'], datetime):
            act['date'] = datetime.combine(act['date'], datetime.min.time())
    activites_recentes.sort(key=lambda x: x['date'], reverse=True)
    activites_recentes = activites_recentes[:5]

    # -------------------------
    # Render template
    # -------------------------
    return render_template(
        'statistiques_avancees.html',
        stats=stats,
        recent_eleves=recent_eleves,
        noms_eleves=noms_eleves,
        absences_par_eleve=absences_par_eleve,
        repartition_notes=repartition_notes,
        classes_labels=classes_labels,
        classes_data=classes_data,
        activites_recentes=activites_recentes,
        periode=periode,
        date_debut=date_debut.strftime('%Y-%m-%d') if isinstance(date_debut, datetime) else '',
        date_fin=date_fin.strftime('%Y-%m-%d') if isinstance(date_fin, datetime) else ''
    )

@main.route('/enseignant/dashboard')
@login_required
@role_required('enseignant', 'professeur')
def enseignant_dashboard():
    """Tableau de bord enseignant avec données personnalisées"""
    professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
    
    if not professeur:
        flash("Profil enseignant non trouvé. Contactez l'administrateur.", "warning")
        return redirect(url_for('main.index'))
    
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
        'enseignant_dashboard.html',
        stats=stats,
        mes_cours=mes_cours,
        dernieres_notes=dernieres_notes,
        emplois=emplois,
        now=now,
        aujourdhui=aujourdhui
    )

@main.route('/enseignant')
@login_required
@role_required('enseignant', 'professeur')
def enseignant_home():
    """Page d'accueil de l'enseignant avec emploi du temps"""
    professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
    
    if not professeur:
        flash("Profil enseignant non trouvé", "danger")
        return redirect(url_for('main.logout'))
    
    emplois = EmploiTemps.query.filter_by(professeur_id=professeur.id).order_by(
        EmploiTemps.jour, EmploiTemps.heure_debut
    ).all()
    
    return render_template('enseignant_home.html', emplois=emplois)
