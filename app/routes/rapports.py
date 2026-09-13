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
from app.services.annees_scolaires import get_annee_consultee
from app.services.statistiques_annuelles import (
    get_absences_par_mois_annuelles,
    get_notes_moyennes_annuelles,
    get_rapport_absences_par_classe_annuel,
    get_rapport_notes_par_classe_annuel,
    get_rapports_annuels,
)


_rapports_cache = {
    'notes_par_classe': None,
    'absences_par_classe': None,
    'timestamp_notes': None,
    'timestamp_absences': None
}
CACHE_DURATION = 60


@main.route('/api/stats/notes_moyennes')
@login_required
@role_required('admin', 'professeur')
def api_stats_notes_moyennes():
    annee_consultee = get_annee_consultee(current_user.ecole_id)
    professeur_id = None
    if current_user.role == 'professeur':
        professeur_id = getattr(getattr(current_user, 'professeur_rel', None), 'id', None)
    return jsonify(get_notes_moyennes_annuelles(current_user.ecole_id, annee_consultee, professeur_id=professeur_id))

@main.route('/api/stats/absences_par_mois')
@login_required
@role_required('admin')
def api_stats_absences_par_mois():
    annee_consultee = get_annee_consultee(current_user.ecole_id)
    return jsonify(get_absences_par_mois_annuelles(current_user.ecole_id, annee_consultee))

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
            Utilisateur.role == 'professeur'
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
            "total_professeurs": Utilisateur.query.filter_by(role='professeur').count(),
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
@role_required('admin')
def rapport_notes_par_classe():
    annee_consultee = get_annee_consultee(current_user.ecole_id)
    return jsonify(get_rapport_notes_par_classe_annuel(current_user.ecole_id, annee_consultee))

@main.route('/rapport/absences_par_classe')
@login_required
@role_required('admin')
def rapport_absences_par_classe():
    annee_consultee = get_annee_consultee(current_user.ecole_id)
    return jsonify(get_rapport_absences_par_classe_annuel(current_user.ecole_id, annee_consultee))

@main.route('/rapports')
@login_required
@role_required('admin')
def rapports():
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    donnees = get_rapports_annuels(ecole_id, annee_consultee)
    return render_template(
        'rapports.html',
        classes=donnees["classes"],
        classes_data=donnees["classes_data"],
        classe_plus_absente=donnees["classe_plus_absente"],
        classe_plus_assidue=donnees["classe_plus_assidue"],
        classe_meilleure_moyenne=donnees["classe_meilleure_moyenne"],
        statistiques=donnees["statistiques"],
        chart_data=donnees["chart_data"],
        annee_consultee=annee_consultee,
        role=current_user.role
    )
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
    if current_user.role in ['admin', 'professeur', 'parent']:
        ecole_id = current_user.ecole_id

    queries = []

    # ---------- ÉLÈVES ----------
    # ---------- ELEVES ----------
    if type_recherche in ['all', 'eleves']:
        annee_recherche = get_annee_consultee(ecole_id) if ecole_id else None
        eleve_query = db.session.query(
            Eleve.id.label('id'),
            Eleve.nom.label('nom'),
            Eleve.prenom.label('prenom'),
            Classe.nom.label('classe'),
            literal('eleve').label('type')
        ).join(Inscription, Inscription.eleve_id == Eleve.id).join(Classe, Classe.id == Inscription.classe_id).filter(
            (Eleve.nom.ilike(f"%{terme}%")) | (Eleve.prenom.ilike(f"%{terme}%"))
        )
        if annee_recherche:
            eleve_query = eleve_query.filter(Inscription.annee_scolaire_id == annee_recherche.id)
        else:
            eleve_query = eleve_query.filter(db.false())

        if classe_id:
            eleve_query = eleve_query.filter(Inscription.classe_id == classe_id)
        if ecole_id:
            eleve_query = eleve_query.filter(Inscription.ecole_id == ecole_id, Classe.ecole_id == ecole_id)

        if current_user.role == 'professeur':
            professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
            if professeur:
                classe_ids = [c.id for c in professeur.classes_assignees.all()]
                eleve_query = eleve_query.filter(Inscription.classe_id.in_(classe_ids))

        elif current_user.role == 'parent':
            eleve_query = eleve_query.filter(Eleve.parent_id == current_user.id)

        queries.append(eleve_query)

    # ---------- PROFESSEURS ----------
    if type_recherche in ['all', 'professeurs'] and current_user.role == 'admin':
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
    if type_recherche in ['all', 'cours'] and current_user.role in ('admin', 'professeur'):
        cours_query = db.session.query(
            Cours.id.label('id'),
            Cours.nom.label('nom'),
            Cours.description.label('description'),
            literal('').label('classe'),
            literal('cours').label('type')
        ).join(Professeur, isouter=True).join(Utilisateur, Professeur.utilisateur_id == Utilisateur.id, isouter=True)

        if ecole_id:
            cours_query = cours_query.filter(Cours.ecole_id == ecole_id)

        if current_user.role == 'professeur':
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
