from . import main
from flask import g
from app.authorization import tenant_required
from .common import (
    Absence,
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
    BytesIO,
    current_user,
    datetime,
    db,
    get_ecole_filter_query,
    jsonify,
    literal,
    login_required,
    render_template,
    request,
    role_required,
    send_file,
    timedelta,
    url_for,
)
from app.services.annees_scolaires import get_annee_consultee
from app.utils_classes import classes_triees_pedagogique
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
@tenant_required
def api_stats_notes_moyennes():
    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    professeur_id = None
    if current_user.role == 'professeur':
        professeur_id = getattr(getattr(current_user, 'professeur_rel', None), 'id', None)
    return jsonify(get_notes_moyennes_annuelles(ecole_id, annee_consultee, professeur_id=professeur_id))

@main.route('/api/stats/absences_par_mois')
@login_required
@role_required('admin')
@tenant_required
def api_stats_absences_par_mois():
    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    return jsonify(get_absences_par_mois_annuelles(ecole_id, annee_consultee))

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
@tenant_required
def rapport_notes_par_classe():
    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    return jsonify(get_rapport_notes_par_classe_annuel(ecole_id, annee_consultee))

@main.route('/rapport/absences_par_classe')
@login_required
@role_required('admin')
@tenant_required
def rapport_absences_par_classe():
    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    return jsonify(get_rapport_absences_par_classe_annuel(ecole_id, annee_consultee))

_rapports_annuels_cache = {}
RAPPORTS_CACHE_TTL = 60


def _get_rapports_annuels_cached(ecole_id, annee_consultee):
    if not ecole_id or not annee_consultee:
        return get_rapports_annuels(ecole_id, annee_consultee)

    key = (ecole_id, getattr(annee_consultee, 'id', None))
    now = datetime.utcnow().timestamp()
    if key in _rapports_annuels_cache:
        ts, data = _rapports_annuels_cache[key]
        if now - ts < RAPPORTS_CACHE_TTL:
            return data

    data = get_rapports_annuels(ecole_id, annee_consultee)
    _rapports_annuels_cache[key] = (now, data)
    return data


@main.route('/rapports')
@login_required
@role_required('admin')
@tenant_required
def rapports():
    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    donnees = _get_rapports_annuels_cached(ecole_id, annee_consultee)
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


def _rapport_export_rows(donnees):
    rows = []
    for c in donnees["classes_data"]:
        finances = c.get("finances", {})
        rows.append({
            "Classe": c["nom"],
            "Niveau": c.get("niveau") or "",
            "Effectif": c["effectif"],
            "Garçons": c["garcons"],
            "Filles": c["filles"],
            "Moyenne": c["moyenne"],
            "Absences": c["total_absences"],
            "Absences justifiées": c["justifiees"],
            "Absences injustifiées": c["non_justifiees"],
            "Frais attendus": finances.get("frais_attendus", 0.0),
            "Total encaissé": finances.get("total_encaisse", 0.0),
            "Reste à payer": finances.get("reste_a_payer", 0.0),
            "Élèves non payés": finances.get("eleves_non_payes", 0),
            "Paiements partiels": finances.get("paiements_partiels", 0),
        })
    return rows


@main.route('/rapports/export_excel')
@login_required
@role_required('admin')
@tenant_required
def export_rapports_excel():
    import pandas as pd

    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    donnees = get_rapports_annuels(ecole_id, annee_consultee)
    stats = donnees["statistiques"]

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(_rapport_export_rows(donnees)).to_excel(writer, sheet_name='Classes', index=False)
        pd.DataFrame([{
            "École": current_user.ecole.nom if current_user.ecole else "",
            "Année": annee_consultee.nom if annee_consultee else "",
            "Total élèves": stats["total_eleves"],
            "Classes": stats["total_classes"],
            "Absences": stats["total_absences"],
            "Moyenne générale": stats["moyenne_generale"],
            "Frais attendus": stats.get("frais_attendus", 0.0),
            "Total encaissé": stats.get("total_encaisse", 0.0),
            "Reste à payer": stats.get("reste_a_payer", 0.0),
            "Date génération": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        }]).to_excel(writer, sheet_name='Synthèse', index=False)
    output.seek(0)

    suffix = f"_{annee_consultee.nom}" if annee_consultee else ""
    return send_file(
        output,
        as_attachment=True,
        download_name=f"rapports_statistiques{suffix}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@main.route('/rapports/export_pdf')
@login_required
@role_required('admin')
@tenant_required
def export_rapports_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

    ecole_id = g.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    donnees = get_rapports_annuels(ecole_id, annee_consultee)
    stats = donnees["statistiques"]
    rows = _rapport_export_rows(donnees)

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("Rapports & Statistiques", styles["Title"]),
        Paragraph(f"École : {current_user.ecole.nom if current_user.ecole else '-'}", styles["Normal"]),
        Paragraph(f"Année : {annee_consultee.nom if annee_consultee else '-'}", styles["Normal"]),
        Paragraph(f"Date génération : {datetime.utcnow().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]),
        Spacer(1, 12),
        Paragraph(
            f"Élèves : {stats['total_eleves']} | Classes : {stats['total_classes']} | "
            f"Absences : {stats['total_absences']} | Moyenne : {stats['moyenne_generale'] or '-'} | "
            f"Encaissé : {stats.get('total_encaisse', 0.0):,.0f} | Reste : {stats.get('reste_a_payer', 0.0):,.0f}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    table_data = [["Classe", "Eff.", "Moy.", "Abs.", "Encaissé", "Reste"]]
    for row in rows:
        table_data.append([
            row["Classe"],
            row["Effectif"],
            row["Moyenne"] if row["Moyenne"] is not None else "-",
            row["Absences"],
            f"{row['Total encaissé']:,.0f}",
            f"{row['Reste à payer']:,.0f}",
        ])
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    elements.append(table)
    doc.build(elements)
    output.seek(0)

    suffix = f"_{annee_consultee.nom}" if annee_consultee else ""
    return send_file(
        output,
        as_attachment=True,
        download_name=f"rapports_statistiques{suffix}.pdf",
        mimetype="application/pdf",
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
                    'lien': url_for('main.voir_eleve', eleve_id=enfant.id),
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
                    'lien': url_for('main.voir_eleve', eleve_id=enfant.id),
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
@tenant_required
def recherche():
    terme = request.args.get('q', '').strip()
    type_recherche = request.args.get('type', 'all')
    classe_id = request.args.get('classe', type=int)
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    per_page = min(max(request.args.get('per_page', 10, type=int) or 10, 1), 30)

    ecole_id = g.ecole_id

    annee_consultee = get_annee_consultee(ecole_id) if ecole_id else None
    classes_query = Classe.query.filter_by(ecole_id=ecole_id) if ecole_id else Classe.query.filter(db.false())
    if annee_consultee:
        classes_query = classes_query.filter(Classe.annee_scolaire_id == annee_consultee.id)

    classe_ids_prof = None
    if current_user.role == 'professeur':
        type_recherche = type_recherche if type_recherche in {'all', 'eleves', 'cours'} else 'all'
        professeur = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
        if professeur:
            classe_ids_prof = {
                row.classe_id
                for row in Cours.query.with_entities(Cours.classe_id)
                .filter(Cours.ecole_id == ecole_id, Cours.professeur_id == professeur.id, Cours.classe_id.isnot(None))
                .all()
            }
        else:
            classe_ids_prof = set()
        classes_query = classes_query.filter(Classe.id.in_(classe_ids_prof)) if classe_ids_prof else classes_query.filter(db.false())
    elif current_user.role == 'parent':
        type_recherche = 'eleves'
    elif current_user.role not in {'admin', 'super_admin'}:
        type_recherche = 'all'

    classes_recherche = classes_triees_pedagogique(classes_query).all()
    classes_recherche_ids = {classe.id for classe in classes_recherche}
    if classe_id and classe_id not in classes_recherche_ids:
        classe_id = None

    if not terme:
        return render_template(
            'recherche.html',
            results=None,
            pagination=None,
            classes_recherche=classes_recherche,
            type_recherche=type_recherche,
            classe_id=classe_id,
        )

    results = {'eleves': [], 'professeurs': [], 'cours': [], 'total': 0}
    pagination = None

    if type_recherche in ['all', 'eleves']:
        like = f"%{terme}%"
        eleve_query = Eleve.query.filter(
            (Eleve.nom.ilike(like)) |
            (Eleve.prenom.ilike(like)) |
            (Eleve.code_parent.ilike(like))
        )

        if ecole_id:
            eleve_query = eleve_query.filter(Eleve.ecole_id == ecole_id)
        elif current_user.role != 'super_admin':
            eleve_query = eleve_query.filter(db.false())

        inscription_ids_query = Inscription.query.with_entities(Inscription.eleve_id)
        if ecole_id:
            inscription_ids_query = inscription_ids_query.filter(Inscription.ecole_id == ecole_id)

        restricted_to_inscriptions = bool(classe_id)
        if classe_id:
            inscription_ids_query = inscription_ids_query.filter(Inscription.classe_id == classe_id)

        if current_user.role == 'professeur':
            inscription_ids_query = inscription_ids_query.filter(Inscription.classe_id.in_(classe_ids_prof))
            restricted_to_inscriptions = True
        elif current_user.role == 'parent':
            eleve_query = eleve_query.filter(Eleve.parent_id == current_user.id)

        if restricted_to_inscriptions:
            eleve_query = eleve_query.filter(Eleve.id.in_(inscription_ids_query))

        pagination_obj = eleve_query.order_by(Eleve.nom.asc(), Eleve.prenom.asc()).paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
        pagination = pagination_obj
        eleves = pagination_obj.items
        results['total'] += pagination_obj.total

        eleve_ids = [e.id for e in eleves]
        historiques = {eleve_id: [] for eleve_id in eleve_ids}
        if eleve_ids:
            inscriptions_query = (
                Inscription.query
                .join(Classe, Classe.id == Inscription.classe_id)
                .join(AnneeScolaire, AnneeScolaire.id == Inscription.annee_scolaire_id)
                .filter(Inscription.eleve_id.in_(eleve_ids))
            )
            if ecole_id:
                inscriptions_query = inscriptions_query.filter(Inscription.ecole_id == ecole_id)
            if classe_id:
                inscriptions_query = inscriptions_query.filter(Inscription.classe_id == classe_id)
            if classe_ids_prof is not None:
                inscriptions_query = inscriptions_query.filter(Inscription.classe_id.in_(classe_ids_prof))

            inscriptions = inscriptions_query.order_by(
                AnneeScolaire.date_debut.desc(),
                AnneeScolaire.nom.desc(),
                Classe.nom.asc(),
            ).all()

            inscription_ids = [i.id for i in inscriptions]
            bulletins_par_inscription = {}
            if inscription_ids:
                bulletins_query = Bulletin.query.filter(Bulletin.inscription_id.in_(inscription_ids))
                if ecole_id:
                    bulletins_query = bulletins_query.filter(Bulletin.ecole_id == ecole_id)
                for bulletin in bulletins_query.order_by(Bulletin.periode.asc(), Bulletin.id.asc()).all():
                    bulletins_par_inscription.setdefault(bulletin.inscription_id, []).append({
                        'id': bulletin.id,
                        'periode': bulletin.periode,
                        'moyenne': bulletin.moyenne_generale,
                    })

            for inscription in inscriptions:
                annee = inscription.annee_scolaire
                classe = inscription.classe
                ecole = inscription.ecole
                historiques.setdefault(inscription.eleve_id, []).append({
                    'inscription_id': inscription.id,
                    'annee_id': annee.id if annee else None,
                    'annee': annee.nom if annee else '',
                    'annee_statut': annee.statut if annee else '',
                    'classe': classe.nom if classe else '',
                    'statut': inscription.statut,
                    'ecole': ecole.nom if ecole else '',
                    'bulletins': bulletins_par_inscription.get(inscription.id, []),
                })

        for eleve in eleves:
            historique = historiques.get(eleve.id, [])
            results['eleves'].append({
                'id': eleve.id,
                'nom': eleve.nom,
                'prenom': eleve.prenom,
                'classe': historique[0]['classe'] if historique else 'Sans inscription',
                'ecole': eleve.ecole.nom if getattr(eleve, 'ecole', None) else '',
                'historique': historique,
            })

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
        for r in prof_query.limit(10).all():
            results['professeurs'].append({'id': r.id, 'nom': r.nom, 'prenom': r.prenom, 'specialite': r.classe})
        results['total'] += len(results['professeurs'])

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
        for r in cours_query.limit(10).all():
            results['cours'].append({'id': r.id, 'nom': r.nom, 'description': r.description, 'classe': r.classe})
        results['total'] += len(results['cours'])

    return render_template(
        'recherche.html',
        results=results,
        terme=terme,
        pagination=pagination,
        classes_recherche=classes_recherche,
        type_recherche=type_recherche,
        classe_id=classe_id,
    )
