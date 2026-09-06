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
from app.services import generer_bulletin_pdf


@main.route('/bulletin_eleve/<int:id>')
@login_required
@role_required('admin', 'professeur', 'parent')
def bulletin_eleve(id):
    """
    Génère le bulletin PDF d’un élève :
    - Sécurisé par école et rôle.
    - Accessible uniquement aux admins, professeurs et parents autorisés.
    """

    # Vérification d’accès spécifique au parent
    if current_user.role == 'parent':
        if not check_parent_access(id):
            flash("Accès non autorisé à cet élève.", "danger")
            return redirect(url_for('main.parent_dashboard'))

        if not bulletins_accessible_pour_parent():
            flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
            return redirect(url_for('main.parent_dashboard'))

    # 🔒 Vérification multi-école
    eleve = Eleve.query.filter_by(id=id).first()
    if not can_access_eleve(eleve):
        flash("Élève introuvable ou appartenant à une autre école.", "danger")
        return redirect(url_for('main.profile'))

    ecole = eleve.ecole

    # 🔹 Calcul des moyennes par cours
    moyennes = (
        Note.query.with_entities(
            Cours.nom.label('cours_nom'),
            (func.sum(Note.valeur * Note.coefficient) / func.sum(Note.coefficient)).label('moyenne')
        )
        .join(Cours, Note.cours_id == Cours.id)
        .filter(Note.eleve_id == id, Note.ecole_id == current_user.ecole_id)
        .group_by(Cours.nom)
        .all()
    )

    moyennes_par_cours = {
        m.cours_nom: round(m.moyenne, 2) if m.moyenne else 0
        for m in moyennes
    }

    moyenne_generale = (
        round(sum(moyennes_par_cours.values()) / len(moyennes_par_cours), 2)
        if moyennes_par_cours else 0
    )

    # 🔹 Notes détaillées par cours
    notes = (
        Note.query.options(joinedload(Note.cours))
        .filter_by(eleve_id=id, ecole_id=current_user.ecole_id)
        .order_by(Note.cours_id, Note.date_evaluation.desc())
        .all()
    )

    notes_par_cours = {}
    for note in notes:
        cours_nom = note.cours.nom if note.cours else "Non renseigné"
        notes_par_cours.setdefault(cours_nom, []).append(note)

    # 🔹 Génération du PDF
    try:
        buffer = generer_bulletin_pdf(
            eleve,
            notes_par_cours,
            moyennes_par_cours,
            moyenne_generale,
            logo_path=ecole.logo_path if ecole and ecole.logo_path else None,
            nom_ecole=ecole.nom if ecole else "École non renseignée",
            adresse_ecole=ecole.adresse if ecole else "-",
            contact_ecole=f"Tél: {ecole.telephone or '-'} - Email: {ecole.email or '-'}" if ecole else "-"
        )

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"bulletin_{eleve.prenom}_{eleve.nom}.pdf",
            mimetype='application/pdf'
        )

    except Exception as e:
        current_app.logger.error(f"Erreur lors de la génération du bulletin : {e}")
        flash("Erreur lors de la génération du bulletin PDF.", "danger")
        return redirect(url_for('main.profile'))


@main.route('/bulletins')
@login_required
def bulletins():
    # 🔒 Vérifier l'accès pour les parents
    if current_user.role == 'parent' and not bulletins_accessible_pour_parent():
        flash("Les bulletins ne sont pas encore disponibles. Ils seront publiés prochainement.", "info")
        return redirect(url_for('main.parent_dashboard'))

    # Récupérer les classes et élèves selon le rôle de l'utilisateur
    if current_user.role == 'parent':
        eleves = Eleve.query.filter_by(parent_id=current_user.id, ecole_id=current_user.ecole_id).all()
        classes = sorted(list({e.classe for e in eleves if e.classe}), key=lambda c: (c.nom or '').lower())
    elif current_user.role == 'professeur':
        professeur = getattr(current_user, 'professeur_rel', None)
        classes_assignees = professeur.classes_assignees.filter_by(ecole_id=current_user.ecole_id).all() if professeur else []
        classes = sorted(classes_assignees, key=lambda c: (c.nom or '').lower())
        classe_ids = [c.id for c in classes]
        eleves = Eleve.query.filter(Eleve.ecole_id == current_user.ecole_id, Eleve.classe_id.in_(classe_ids)).all() if classe_ids else []
    elif current_user.role == 'admin':
        classes = Classe.query.filter_by(ecole_id=current_user.ecole_id).order_by(Classe.nom.asc()).all()
        eleves = Eleve.query.filter_by(ecole_id=current_user.ecole_id).all()
    else:
        classes = []
        eleves = []

    # Optimisation : récupération de toutes les notes en 1 seule requête SQL groupée
    eleve_ids = [e.id for e in eleves]
    notes_all = (
        Note.query.options(joinedload(Note.cours))
        .filter(Note.eleve_id.in_(eleve_ids), Note.ecole_id == current_user.ecole_id)
        .all()
    ) if eleve_ids else []

    notes_par_eleve = {}
    for n in notes_all:
        notes_par_eleve.setdefault(n.eleve_id, []).append(n)

    # Calcul des moyennes et mentions individuelles
    eleves_avec_moyennes = []
    for eleve in eleves:
        student_notes = notes_par_eleve.get(eleve.id, [])
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
            'eleve': eleve,
            'moyenne': moyenne,
            'notes_count': len(student_notes),
            'appreciation': appreciation,
            'appreciation_code': appreciation_code,
            'badge_class': badge_class,
            'notes': student_notes,
            'rang_classe': None,
            'rang_classe_total': 0
        })

    # Regroupement des élèves par classe
    eleves_par_classe = {c.id: [] for c in classes}
    eleves_sans_classe = []

    for item in eleves_avec_moyennes:
        e = item['eleve']
        if e.classe_id and e.classe_id in eleves_par_classe:
            eleves_par_classe[e.classe_id].append(item)
        elif e.classe and e.classe.id in eleves_par_classe:
            eleves_par_classe[e.classe.id].append(item)
        else:
            eleves_sans_classe.append(item)

    # Calcul des rangs au sein de chaque classe et des statistiques par classe
    classe_stats = {}
    for c in classes:
        c_items = eleves_par_classe.get(c.id, [])
        c_evalues = [it for it in c_items if it['notes_count'] > 0]
        c_non_evalues = [it for it in c_items if it['notes_count'] == 0]

        # Tri : évalués par moyenne décroissante, puis non-évalués par nom
        c_evalues.sort(key=lambda x: x['moyenne'], reverse=True)
        c_non_evalues.sort(key=lambda x: ((x['eleve'].nom or '').lower(), (x['eleve'].prenom or '').lower()))

        # Rangs internes à la classe
        for rank, it in enumerate(c_evalues, 1):
            it['rang_classe'] = rank
            it['rang_classe_total'] = len(c_evalues)
        for it in c_non_evalues:
            it['rang_classe'] = None
            it['rang_classe_total'] = len(c_evalues)

        c_items_sorted = c_evalues + c_non_evalues
        eleves_par_classe[c.id] = c_items_sorted

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

    # Traitement des élèves sans classe
    if eleves_sans_classe:
        c_evalues = [it for it in eleves_sans_classe if it['notes_count'] > 0]
        c_non_evalues = [it for it in eleves_sans_classe if it['notes_count'] == 0]
        c_evalues.sort(key=lambda x: x['moyenne'], reverse=True)
        c_non_evalues.sort(key=lambda x: ((x['eleve'].nom or '').lower(), (x['eleve'].prenom or '').lower()))
        for rank, it in enumerate(c_evalues, 1):
            it['rang_classe'] = rank
            it['rang_classe_total'] = len(c_evalues)
        for it in c_non_evalues:
            it['rang_classe'] = None
            it['rang_classe_total'] = len(c_evalues)
        eleves_sans_classe = c_evalues + c_non_evalues

    # Tri global par moyenne décroissante pour rétrocompatibilité
    eleves_avec_moyennes.sort(key=lambda x: x['moyenne'], reverse=True)

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

    periode_active = PeriodeBulletin.query.filter_by(ecole_id=current_user.ecole_id, periode_active=True).first()
    if not periode_active:
        periode_active = PeriodeBulletin.query.filter_by(ecole_id=current_user.ecole_id, publie=True).first()

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
        total_eleves=len(eleves),
        bulletins_accessibles=bulletins_accessible_pour_parent()
    )


@main.route('/toggle_periode/<int:id>')
@login_required
@role_required('admin')
def toggle_periode(id):
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()
    periode.publie = not periode.publie  # on inverse l’état
    if periode.publie:
        periode.date_publication = datetime.utcnow()
    db.session.commit()
    flash(f"Période {periode.nom} {'activée' if periode.publie else 'désactivée'} avec succès.", "success")
    return redirect(url_for('main.gestion_periodes'))


@main.route('/periodes')
@login_required
@role_required('admin')
def gestion_periodes():
    periodes = get_ecole_filter_query(PeriodeBulletin).all()
    return render_template("gestion_periodes.html", periodes=periodes)


@main.route('/activer_periode/<int:id>')
@login_required
@role_required('admin')
def activer_periode(id):
    """Rendre une période active (une seule période active à la fois)"""
    # Désactiver toutes les périodes
    PeriodeBulletin.query.filter_by(ecole_id=current_user.ecole_id).update({'periode_active': False})
    
    # Activer la période sélectionnée
    periode = PeriodeBulletin.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()
    periode.periode_active = True
    periode.publie = True  # S'assurer qu'elle est publiée
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
    
    # Remplir les choix de l'année scolaire
    form.annee_id.choices = [(a.id, a.nom) for a in AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).all()]
    
    if form.validate_on_submit():
        nom = form.nom.data
        annee_id = form.annee_id.data
        annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=current_user.ecole_id).first()
        if not annee:
            flash("Annee scolaire invalide pour cette ecole.", "danger")
            return redirect(url_for('main.creer_periode'))
        
        # Créer la période
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
    
    # GET - Afficher le formulaire
    return render_template('creer_periode.html', form=form)
