from . import main
from .common import (
    Absence,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
    current_user,
    datetime,
    db,
    filtre_par_ecole,
    func,
    get_ecole_filter_query,
    jsonify,
    literal,
    login_required,
    render_template,
    request,
    role_required,
    timedelta,
    url_for,
)
from app.services import get_cache, set_cache


_rapports_cache = {
    'notes_par_classe': None,
    'absences_par_classe': None,
    'timestamp_notes': None,
    'timestamp_absences': None
}
CACHE_DURATION = 60


@main.route('/api/stats/notes_moyennes')
@login_required
@role_required('admin', 'enseignant')
def api_stats_notes_moyennes():
    """Retourne les moyennes de notes par matière avec cache sécurisé."""
    user_id = current_user.id

    # Vérification du cache
    cached = get_cache(user_id, 'notes_moyennes')
    if cached:
        return jsonify(cached)

    # Filtrage par école
    if current_user.role == 'enseignant':
        cours_ids = [c.id for c in filtre_par_ecole(
            Cours.query.filter_by(professeur_id=current_user.id), Cours
        ).all()]
        result = db.session.query(
            Cours.nom,
            func.avg(Note.valeur).label('moyenne')
        ).join(Note).filter(Note.cours_id.in_(cours_ids)).group_by(Cours.nom).all()
    else:  # admin
        result = db.session.query(
            Cours.nom,
            func.avg(Note.valeur).label('moyenne')
        ).join(Note).group_by(Cours.nom).all()

    data = {
        'matieres': [r[0] for r in result],
        'moyennes': [float(r[1]) if r[1] else 0 for r in result]
    }

    # Mise en cache
    set_cache(user_id, 'notes_moyennes', data)
    return jsonify(data)

@main.route('/api/stats/absences_par_mois')
@login_required
@role_required('admin')
def api_stats_absences_par_mois():
    """Retourne le nombre d'absences par mois (derniers 6 mois) avec cache sécurisé."""
    user_id = current_user.id

    # Vérification du cache
    cached = get_cache(user_id, 'absences_par_mois')
    if cached:
        return jsonify(cached)

    six_mois = datetime.now() - timedelta(days=180)

    # Filtrage par école
    absences_query = filtre_par_ecole(
        Absence.query.filter(Absence.date_absence >= six_mois), Absence
    )

    # Compatibilité SQLite / PostgreSQL
    try:
        result = db.session.query(
            func.strftime('%Y', Absence.date_absence).label('annee'),
            func.strftime('%m', Absence.date_absence).label('mois'),
            func.count(Absence.id).label('total')
        ).filter(Absence.id.in_(absences_query.with_entities(Absence.id))).group_by('annee', 'mois').order_by('annee', 'mois').all()
    except Exception:
        # PostgreSQL
        result = db.session.query(
            func.extract('year', Absence.date_absence).label('annee'),
            func.extract('month', Absence.date_absence).label('mois'),
            func.count(Absence.id).label('total')
        ).filter(Absence.id.in_(absences_query.with_entities(Absence.id))).group_by('annee', 'mois').order_by('annee', 'mois').all()

    mois_labels = [f"{int(r.mois)}/{int(r.annee)}" for r in result]
    absences_data = [int(r.total) for r in result]

    data = {'mois': mois_labels, 'absences': absences_data}

    # Mise en cache
    set_cache(user_id, 'absences_par_mois', data)
    return jsonify(data)

@main.route('/profile')
@login_required
def profile():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('limit', 10, type=int), 100)
    search = request.args.get('search', '', type=str)
    role_filter = request.args.get('role', '', type=str)
    ecole_filter = request.args.get('ecole', '', type=str)
    classe_filter = request.args.get('classe', '', type=str)
    statut_filter = request.args.get('statut', '', type=str)
    sort = request.args.get('sort', 'nom', type=str)
    order = request.args.get('order', 'asc', type=str)

    # Base query
    query = Utilisateur.query

    # ---------------------- Gestion par rôle ----------------------
    if current_user.role == 'super_admin':
        query = query.filter(Utilisateur.role.in_(['admin', 'super_admin']))
        ecoles = get_ecole_filter_query(Ecole).all()
        classes = []
        eleves = []

    elif current_user.role == 'admin':
        query = query.filter(
            Utilisateur.ecole_id == current_user.ecole_id,
            Utilisateur.role != 'super_admin'
        )
        ecoles = [current_user.ecole] if current_user.ecole else []
        classes = Classe.query.filter_by(ecole_id=current_user.ecole_id).all()

        # Pagination des élèves
        eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).limit(100).all()

        # Filtre par classe via les parents d'eleves, Utilisateur n'a pas classe_id
        if classe_filter and classe_filter.isdigit():
            classe_id = int(classe_filter)
            classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first()
            if classe:
                parent_ids = [
                    parent_id for (parent_id,) in
                    Eleve.query.with_entities(Eleve.parent_id)
                    .filter_by(ecole_id=current_user.ecole_id, classe_id=classe_id)
                    .filter(Eleve.parent_id.isnot(None))
                    .all()
                ]
                query = query.filter(Utilisateur.id.in_(parent_ids)) if parent_ids else query.filter(False)

    else:
        query = query.filter(Utilisateur.id == current_user.id)
        ecoles = []
        classes = []
        eleves = []

    # ---------------------- Filtres supplémentaires ----------------------
    if search:
        query = query.filter(
            db.or_(
                Utilisateur.nom.ilike(f"%{search}%"),
                Utilisateur.prenom.ilike(f"%{search}%"),
                Utilisateur.email.ilike(f"%{search}%")
            )
        )

    if role_filter:
        query = query.filter(Utilisateur.role == role_filter)

    if ecole_filter and current_user.role == 'super_admin' and ecole_filter.isdigit():
        query = query.filter(Utilisateur.ecole_id == int(ecole_filter))

    if statut_filter:
        query = query.filter(Utilisateur.statut == statut_filter)

    # ---------------------- Tri sécurisé ----------------------
    colonnes_autorisees = ['nom', 'prenom', 'email', 'role', 'statut']
    if sort not in colonnes_autorisees:
        sort = 'nom'
    sort_col = getattr(Utilisateur, sort)
    sort_col = sort_col.desc() if order == 'desc' else sort_col.asc()
    query = query.order_by(sort_col)

    # ---------------------- Pagination principale ----------------------
    utilisateurs = query.paginate(page=page, per_page=per_page, error_out=False)

    # ---------------------- Professeurs et parents pour admin ----------------------
    professeurs = []
    parents = []
    if current_user.role == 'admin':
        professeurs = Utilisateur.query.filter(
            Utilisateur.ecole_id == current_user.ecole_id,
            Utilisateur.role == 'enseignant'
        ).limit(100).all()

        parents = Utilisateur.query.filter(
            Utilisateur.ecole_id == current_user.ecole_id,
            Utilisateur.role == 'parent'
        ).limit(100).all()

        # Chargement des enfants pour éviter N+1
        parent_ids = [p.id for p in parents]
        enfants = Eleve.query.filter(Eleve.parent_id.in_(parent_ids)).all()
        enfants_par_parent = {}
        for e in enfants:
            enfants_par_parent.setdefault(e.parent_id, []).append(e)
        for p in parents:
            p.enfants_list = enfants_par_parent.get(p.id, [])

    # ---------------------- Statistiques ----------------------
    if current_user.role == 'super_admin':
        statistiques = {
            "total_eleves": Eleve.query.count(),
            "total_professeurs": Utilisateur.query.filter_by(role='enseignant').count(),
            "total_classes": Classe.query.count(),
            "total_ecoles": Ecole.query.count(),
            "taux_occupation": 75
        }
    elif current_user.role == 'admin':
        total_capacite = sum(classe.capacite_max for classe in classes) if classes else 0
        total_eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).count()
        taux_occupation = int((total_eleves / total_capacite) * 100) if total_capacite > 0 else 0
        statistiques = {
            "total_eleves": total_eleves,
            "total_professeurs": len(professeurs),
            "total_classes": len(classes),
            "taux_occupation": taux_occupation
        }
    else:
        statistiques = None

    return render_template(
        'profile.html',
        utilisateurs=utilisateurs,
        professeurs=professeurs,
        parents=parents,
        eleves=eleves,
        ecoles=ecoles,
        classes=classes,
        search=search,
        role_filter=role_filter,
        sort=sort,
        order=order,
        statistiques=statistiques
    )

@main.route('/rapport/notes_par_classe')
@login_required
@role_required('admin', 'super_admin')
def rapport_notes_par_classe():
    now = datetime.now()
    if _rapports_cache['notes_par_classe'] and (now - _rapports_cache['timestamp_notes']).total_seconds() < CACHE_DURATION:
        return jsonify(_rapports_cache['notes_par_classe'])

    # Filtrage selon rôle
    query_eleves = Eleve.query.options(db.joinedload(Eleve.notes), db.joinedload(Eleve.classe))
    if current_user.role == 'admin':
        query_eleves = query_eleves.filter(Eleve.ecole_id == current_user.ecole_id)

    eleves = query_eleves.all()

    classes_dict = {}
    for e in eleves:
        classe_nom = e.classe.nom if e.classe else "Non assigné"
        classes_dict.setdefault(classe_nom, []).append(e)

    data = {}
    for classe_nom in sorted(classes_dict.keys()):
        eleves_classe = classes_dict[classe_nom]
        notes = [n for e in eleves_classe for n in e.notes]
        if notes:
            total_pondere = sum(n.valeur * n.coefficient for n in notes)
            total_coefficients = sum(n.coefficient for n in notes)
            moyenne_classe = round(total_pondere / total_coefficients, 2)
        else:
            moyenne_classe = 0
        data[classe_nom] = moyenne_classe

    _rapports_cache['notes_par_classe'] = data
    _rapports_cache['timestamp_notes'] = now

    return jsonify(data)

@main.route('/rapport/absences_par_classe')
@login_required
@role_required('admin', 'super_admin')
def rapport_absences_par_classe():
    now = datetime.now()
    if _rapports_cache['absences_par_classe'] and (now - _rapports_cache['timestamp_absences']).total_seconds() < CACHE_DURATION:
        return jsonify(_rapports_cache['absences_par_classe'])

    query_eleves = Eleve.query.options(db.joinedload(Eleve.absences), db.joinedload(Eleve.classe))
    if current_user.role == 'admin':
        query_eleves = query_eleves.filter(Eleve.ecole_id == current_user.ecole_id)

    eleves = query_eleves.all()

    classes_dict = {}
    for e in eleves:
        classe_nom = e.classe.nom if e.classe else "Non assigné"
        classes_dict.setdefault(classe_nom, []).append(e)

    data = {}
    for classe_nom in sorted(classes_dict.keys()):
        eleves_classe = classes_dict[classe_nom]
        absences_count = sum(len(e.absences) for e in eleves_classe)
        data[classe_nom] = absences_count

    _rapports_cache['absences_par_classe'] = data
    _rapports_cache['timestamp_absences'] = now

    return jsonify(data)

@main.route('/rapports')
@login_required
@role_required('admin', 'super_admin')
def rapports():
    # Classes filtrées selon rôle
    classes_query = Classe.query
    if current_user.role == 'admin':
        classes_query = classes_query.filter(Classe.ecole_id == current_user.ecole_id)
    classes = classes_query.all()

    # Statistiques globales
    if current_user.role == 'admin':
        total_eleves = Eleve.query.filter(Eleve.ecole_id == current_user.ecole_id).count()
        total_professeurs = Utilisateur.query.filter_by(role='enseignant', ecole_id=current_user.ecole_id).count()
        total_classes = len(classes)
        capacite_totale = sum(c.capacite_max for c in classes) if classes else 1
    else:  # super_admin
        total_eleves = Eleve.query.count()
        total_professeurs = Utilisateur.query.filter_by(role='enseignant').count()
        total_classes = Classe.query.count()
        capacite_totale = sum(c.capacite_max for c in Classe.query.all()) if total_classes > 0 else 1

    taux_occupation = round((total_eleves / capacite_totale) * 100, 2) if capacite_totale > 0 else 0

    statistiques = {
        'total_eleves': total_eleves,
        'total_professeurs': total_professeurs,
        'total_classes': total_classes,
        'taux_occupation': taux_occupation
    }

    return render_template('rapports.html', classes=classes, role=current_user.role, statistiques=statistiques)

@main.route('/notifications')
@login_required
def notifications():
    """
    Retourne les notifications pour l'utilisateur courant,
    avec filtrage multi-école pour les admins.
    """
    notifications = []
    now = datetime.now()

    # --- Admin ---
    if current_user.role == 'admin':
        # Paiements en attente uniquement pour l'école de l'admin
        paiements_attente = Paiement.query.join(Eleve).filter(
            Paiement.statut == 'en attente',
            Eleve.ecole_id == current_user.ecole_id
        ).count()
        if paiements_attente > 0:
            notifications.append({
                'type': 'warning',
                'message': f'{paiements_attente} paiement(s) en attente de validation',
                'lien': url_for('main.paiements'),
                'date': now.strftime("%d/%m/%Y %H:%M"),
                'priority': 2
            })

        # Nouvelles inscriptions ce mois-ci (filtrées par école)
        nouvelles_inscriptions = Eleve.query.filter(
            Eleve.ecole_id == current_user.ecole_id,
            Eleve.date_inscription >= now.replace(day=1)
        ).count()
        if nouvelles_inscriptions > 0:
            notifications.append({
                'type': 'info',
                'message': f'{nouvelles_inscriptions} nouvelle(s) inscription(s) ce mois-ci',
                'lien': url_for('main.eleves'),
                'date': now.strftime("%d/%m/%Y %H:%M"),
                'priority': 1
            })

    # --- Parent ---
    elif current_user.role == 'parent':
        # Récupérer uniquement ses propres enfants
        enfants = Eleve.query.filter_by(parent_id=current_user.id).all()
        for enfant in enfants:
            # Notes des 7 derniers jours
            nouvelles_notes = Note.query.filter(
                Note.eleve_id == enfant.id,
                Note.date_evaluation >= now - timedelta(days=7)
            ).count()
            if nouvelles_notes > 0:
                notifications.append({
                    'type': 'info',
                    'message': f'{nouvelles_notes} nouvelle(s) note(s) pour {enfant.prenom}',
                    'lien': url_for('main.portal_parent'),
                    'date': now.strftime("%d/%m/%Y %H:%M"),
                    'priority': 2
                })

            # Absences non justifiées des 7 derniers jours
            absences_non_justifiees = Absence.query.filter(
                Absence.eleve_id == enfant.id,
                Absence.justifiee == False,
                Absence.date_absence >= now - timedelta(days=7)
            ).count()
            if absences_non_justifiees > 0:
                notifications.append({
                    'type': 'warning',
                    'message': f'{absences_non_justifiees} absence(s) non justifiée(s) pour {enfant.prenom}',
                    'lien': url_for('main.portal_parent'),
                    'date': now.strftime("%d/%m/%Y %H:%M"),
                    'priority': 3
                })

    # --- Tri des notifications par priorité décroissante ---
    notifications.sort(key=lambda n: n['priority'], reverse=True)

    if request.args.get('format') == 'json':
        return jsonify(notifications)

    return render_template("notifications.html", notifications=notifications)

@main.route('/recherche')
@login_required
def recherche():
    terme = request.args.get('q', '').strip()
    type_recherche = request.args.get('type', 'all')
    classe_id = request.args.get('classe', type=int)

    if not terme:
        return render_template('recherche.html', results=None)

    ecole_id = None
    if current_user.role in ['admin', 'enseignant']:
        ecole_id = current_user.ecole_id

    queries = []

    # ---------- ÉLÈVES ----------
    if type_recherche in ['all', 'eleves']:
        eleve_query = db.session.query(
            Eleve.id.label('id'),
            Eleve.nom.label('nom'),
            Eleve.prenom.label('prenom'),
            Classe.nom.label('classe'),
            literal('eleve').label('type')
        ).join(Classe, isouter=True).filter(
            (Eleve.nom.ilike(f"%{terme}%")) | (Eleve.prenom.ilike(f"%{terme}%"))
        )

        if classe_id:
            eleve_query = eleve_query.filter(Eleve.classe_id == classe_id)
        if ecole_id:
            eleve_query = eleve_query.filter(Classe.ecole_id == ecole_id)

        if current_user.role == 'enseignant':
            professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
            if professeur:
                cours_ids = db.session.query(Cours.id).filter_by(professeur_id=professeur.id).subquery()
                eleve_ids = db.session.query(Note.eleve_id).filter(Note.cours_id.in_(cours_ids)).subquery()
                eleve_query = eleve_query.filter(Eleve.id.in_(eleve_ids))

        elif current_user.role == 'parent':
            eleve_query = eleve_query.filter(Eleve.parent_id == current_user.id)

        queries.append(eleve_query)

    # ---------- PROFESSEURS ----------
    if type_recherche in ['all', 'professeurs'] and current_user.role != 'enseignant':
        prof_query = db.session.query(
            Professeur.id.label('id'),
            Professeur.nom.label('nom'),
            Professeur.prenom.label('prenom'),
            Professeur.specialite.label('classe'),
            literal('professeur').label('type')
        ).join(Utilisateur)

        if ecole_id:
            prof_query = prof_query.filter(Utilisateur.ecole_id == ecole_id)

        prof_query = prof_query.filter(
            (Professeur.nom.ilike(f"%{terme}%")) |
            (Professeur.prenom.ilike(f"%{terme}%")) |
            (Professeur.specialite.ilike(f"%{terme}%"))
        )
        queries.append(prof_query)

    # ---------- COURS ----------
    if type_recherche in ['all', 'cours']:
        cours_query = db.session.query(
            Cours.id.label('id'),
            Cours.nom.label('nom'),
            Cours.description.label('description'),
            literal('').label('classe'),
            literal('cours').label('type')
        ).join(Professeur, isouter=True).join(Utilisateur, Professeur.utilisateur_id == Utilisateur.id, isouter=True)

        if ecole_id:
            cours_query = cours_query.filter(Utilisateur.ecole_id == ecole_id)

        if current_user.role == 'enseignant':
            professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
            if professeur:
                cours_query = cours_query.filter(Cours.professeur_id == professeur.id)

        cours_query = cours_query.filter(
            (Cours.nom.ilike(f"%{terme}%")) | (Cours.description.ilike(f"%{terme}%"))
        )
        queries.append(cours_query)

    # Union de toutes les requêtes (sans limit dans les sous-requêtes)
    if queries:
        final_query = queries[0]
        for q in queries[1:]:
            final_query = final_query.union_all(q)
        results_raw = final_query.limit(30).all()  # Limite globale après l'union
    else:
        results_raw = []

    results = {'eleves': [], 'professeurs': [], 'cours': [], 'total': len(results_raw)}
    for r in results_raw:
        if r.type == 'eleve':
            results['eleves'].append({'id': r.id, 'nom': r.nom, 'prenom': r.prenom, 'classe': r.classe})
        elif r.type == 'professeur':
            results['professeurs'].append({'id': r.id, 'nom': r.nom, 'prenom': r.prenom, 'specialite': r.classe})
        elif r.type == 'cours':
            results['cours'].append({'id': r.id, 'nom': r.nom, 'description': r.description, 'classe': r.classe})

    return render_template('recherche.html', results=results, terme=terme)
