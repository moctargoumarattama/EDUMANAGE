from . import main
from .common import (
    abort,
    AnneeScolaire,
    can_access_class,
    Classe,
    ClasseForm,
    Cours,
    Eleve,
    Inscription,
    Note,
    Professeur,
    professeur_classes,
    current_app,
    current_user,
    db,
    ecole_required,
    flash,
    get_ecole_filter_query,
    joinedload,
    jsonify,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    session,
    url_for,
)
from app.services import get_statistics
from app.services.annees_scolaires import get_annee_consultee, get_classes_annee
from app.services.niveaux import creer_classe_depuis_niveau, get_niveau_configs_grouped, modifier_classe_depuis_niveau, set_cycle_actif, set_niveau_actif


@main.route('/api/classes')
@login_required
@role_required('admin', 'professeur')
@ecole_required
def api_classes():
    """Retourne la liste des classes filtrée par école et année active (JSON)"""
    # Détermination de l'école
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        return jsonify([]), 403  # Super-admin sans école sélectionnée

    # Récupération de l'année scolaire active
    annee_consultee = get_annee_consultee(ecole_id, request.args.get("annee_id", type=int))

    # Filtrage des classes
    classes_query = Classe.query.filter_by(ecole_id=ecole_id)
    if current_user.role == 'professeur':
        professeur = current_user.get_professeur()
        if not professeur:
            return jsonify([]), 403
        classes_query = classes_query.filter(
            db.or_(
                Classe.professeur_id == professeur.id,
                Classe.id.in_(
                    db.session.query(professeur_classes.c.classe_id)
                    .filter(professeur_classes.c.professeur_id == professeur.id)
                )
            )
        )
    if annee_consultee:
        classes_query = classes_query.filter_by(annee_scolaire_id=annee_consultee.id)
    classes = classes_query.order_by(Classe.nom).all()

    return jsonify([{'id': c.id, 'nom': c.nom} for c in classes])

@main.route("/classes")
@login_required
def liste_classes():
    page = request.args.get('page', 1, type=int)
    per_page = 25  # Affichage confortable pour les longues listes

    # Filtres
    search = request.args.get('search', '')
    niveau = request.args.get('niveau', '')
    sort_by = request.args.get('sort', 'nom')

    # Base query pour l'école de l'utilisateur
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        flash("Veuillez selectionner une ecole.", "warning")
        return redirect(url_for("main.index"))

    annee_consultee = get_annee_consultee(ecole_id, request.args.get("annee_id", type=int))
    base_query = get_classes_annee(ecole_id, annee_consultee.id) if annee_consultee else Classe.query.filter_by(ecole_id=ecole_id).filter(db.false())

    if current_user.role == 'professeur':
        professeur = current_user.get_professeur()
        if professeur:
            base_query = base_query.filter(
                db.or_(
                    Classe.professeur_id == professeur.id,
                    Classe.id.in_(
                        db.session.query(professeur_classes.c.classe_id)
                        .filter(professeur_classes.c.professeur_id == professeur.id)
                    )
                )
            )
        else:
            return render_template("classes.html", classes=[], **get_statistics([]))

    # Appliquer les filtres
    if search:
        base_query = base_query.filter(Classe.nom.ilike(f'%{search}%'))

    if niveau:
        base_query = base_query.filter(Classe.niveau == niveau)

    # Appliquer le tri
    if sort_by == 'effectif':
        base_query = base_query.order_by(Classe.effectif.desc())
    elif sort_by == 'niveau':
        base_query = base_query.order_by(Classe.niveau)
    else:  # tri par nom par défaut
        base_query = base_query.order_by(Classe.nom)

    # Pagination
    classes_paginated = base_query.paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Récupérer toutes les classes pour les statistiques (sans pagination)
    all_classes = base_query.all()

    # Calculer les valeurs pour la pagination
    start_item = ((page - 1) * per_page) + 1
    end_item = min(page * per_page, classes_paginated.total)

    return render_template(
        "classes.html",
        classes=classes_paginated.items,
        pagination=classes_paginated,
        total_eleves=sum(c.effectif_reel for c in all_classes),
        moyenne_effectif=int(sum(c.effectif_reel for c in all_classes) / len(all_classes)) if all_classes else 0,
        classes_pleines=sum(1 for c in all_classes if c.effectif_reel >= (c.capacite or c.capacite_max or 35)),
        current_filters={
            'search': search,
            'niveau': niveau,
            'sort': sort_by
        },
        start_item=start_item,
        end_item=end_item
    )

@main.route("/classes/add", methods=["GET", "POST"])
@login_required
@role_required('admin')
def ajouter_classe():
    from app.models import AnneeScolaire, Professeur, Classe

    # Récupération obligatoire de l'année scolaire active pour l'école
    annee_consultee = get_annee_consultee(current_user.ecole_id, request.args.get("annee_id", type=int))
    if annee_consultee and annee_consultee.statut == "archivee":
        flash("Impossible de creer une classe dans une annee archivee.", "warning")
        return redirect(url_for("main.liste_classes"))

    annee_active = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id, statut='active').first()
    if not annee_active:
        flash("Veuillez d'abord activer une année scolaire pour votre établissement avant d'ajouter une classe.", "warning")
        return redirect(url_for("main.gestion_annees"))

    form = ClasseForm()
    # Professeurs filtrés par école
    professeurs = get_ecole_filter_query(Professeur).filter_by(ecole_id=current_user.ecole_id).order_by(Professeur.nom).all()
    form.professeur_principal_id.choices = [(0, "--- Aucun professeur principal ---")] + [
        (p.id, f"{p.prenom} {p.nom}") for p in professeurs
    ]
    form.annee_scolaire_id.choices = [(annee_active.id, annee_active.nom)]
    form.annee_scolaire_id.data = annee_active.id

    if form.validate_on_submit():
        try:
            prof_id = form.professeur_principal_id.data if (form.professeur_principal_id.data and form.professeur_principal_id.data > 0) else None
            capacite_val = form.capacite.data or form.effectif.data or 35
            classe, error_msg = creer_classe_depuis_niveau(
                ecole_id=current_user.ecole_id,
                annee_scolaire_id=annee_active.id,
                niveau_id=form.niveau_id.data,
                nom=form.nom.data,
                section=form.section.data,
                capacite=capacite_val,
                professeur_id=prof_id,
            )
            if error_msg:
                flash(error_msg, "warning")
                return redirect(url_for("main.ajouter_classe"))
            flash(f"Classe '{classe.nom}' ajoutee avec succes pour l'annee {annee_active.nom}.", "success")
            return redirect(url_for("main.liste_classes"))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur ajout classe : {e}")
            flash("Erreur lors de l'ajout de la classe.", "danger")
            return redirect(url_for("main.ajouter_classe"))

    return render_template(
        "add_class.html",
        form=form,
        professeurs=professeurs,
        annee_active=annee_active
    )

@main.route("/classes/<int:classe_id>")
@login_required
@role_required('admin', 'professeur')
def detail_classe(classe_id):
    if current_user.role == 'professeur':
        professeur = current_user.get_professeur()
        if not professeur:
            flash("Acces non autorise a cette classe.", "danger")
            return redirect(url_for('main.liste_classes'))
        classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
        is_assigned = bool(
            classe.professeur_id == professeur.id
            or db.session.query(professeur_classes).filter(
                professeur_classes.c.professeur_id == professeur.id,
                professeur_classes.c.classe_id == classe.id
            ).first()
        )
        if not is_assigned:
            abort(403)
    else:
        classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
    from app.models import Absence, Note, Cours, Eleve, Professeur

    # 1. Liste des élèves réels de la classe
    eleves = Eleve.query.filter_by(classe_id=classe.id, ecole_id=classe.ecole_id).order_by(Eleve.nom.asc(), Eleve.prenom.asc()).all()
    total_eleves = len(eleves)
    capacite = classe.capacite or classe.capacite_max or 35
    taux_remplissage = round((total_eleves / capacite) * 100) if capacite > 0 else 0

    # 2. Répartition réelle par genre
    filles_count = sum(1 for e in eleves if (e.genre or '').upper() == 'F')
    garcons_count = sum(1 for e in eleves if (e.genre or '').upper() in ['M', 'H', 'G'])

    # 3. Statistiques réelles des absences
    eleve_ids = [e.id for e in eleves]
    total_absences = Absence.query.filter(Absence.eleve_id.in_(eleve_ids)).count() if eleve_ids else 0
    absences_justifiees = Absence.query.filter(Absence.eleve_id.in_(eleve_ids), Absence.justifiee == True).count() if eleve_ids else 0
    absences_non_justifiees = total_absences - absences_justifiees

    # 4. Données détaillées par élève
    eleves_details = []
    for e in eleves:
        nb_abs = Absence.query.filter_by(eleve_id=e.id).count()
        notes_e = [n.valeur for n in (e.notes or []) if n.valeur is not None]
        moyenne_e = round(sum(notes_e) / len(notes_e), 2) if notes_e else None

        parent_nom = f"{e.parent.prenom or ''} {e.parent.nom}".strip() if e.parent else (e.contact_parent or "Non renseigné")
        parent_tel = e.parent.telephone if (e.parent and e.parent.telephone) else (e.contact_parent or "Non renseigné")
        parent_email = e.parent.email if (e.parent and e.parent.email) else (e.email_parent or "")

        eleves_details.append({
            'id': e.id,
            'nom': e.nom,
            'prenom': e.prenom,
            'genre': e.genre or 'M',
            'date_naissance': e.date_naissance,
            'contact_parent': parent_tel,
            'parent_nom': parent_nom,
            'email_parent': parent_email,
            'statut': e.statut or 'Actif',
            'nb_absences': nb_abs,
            'moyenne': moyenne_e
        })

    # 5. Moyennes réelles par matière
    cours_classe = Cours.query.filter_by(classe_id=classe.id).all()
    matieres_stats = []
    for c in cours_classe:
        notes_cours = Note.query.filter(Note.cours_id == c.id, Note.eleve_id.in_(eleve_ids)).all() if eleve_ids else []
        notes_vals = [n.valeur for n in notes_cours if n.valeur is not None]
        avg = round(sum(notes_vals) / len(notes_vals), 2) if notes_vals else None
        matieres_stats.append({
            'id': c.id,
            'nom': c.nom,
            'coefficient': c.coefficient,
            'professeur': f"{c.professeur.prenom} {c.professeur.nom}" if c.professeur else "Non assigné",
            'moyenne': avg,
            'nb_notes': len(notes_vals)
        })

    # Moyenne générale de la classe
    all_notes = Note.query.filter(Note.eleve_id.in_(eleve_ids)).all() if eleve_ids else []
    all_notes_vals = [n.valeur for n in all_notes if n.valeur is not None]
    moyenne_generale_classe = round(sum(all_notes_vals) / len(all_notes_vals), 2) if all_notes_vals else None

    # 6. Professeurs réels intervenants
    profs_intervenants = {}
    if classe.professeur:
        profs_intervenants[classe.professeur.id] = {
            'prof': classe.professeur,
            'role': 'Professeur principal',
            'matieres': classe.professeur.specialite or 'Principal'
        }
    for c in cours_classe:
        if c.professeur:
            if c.professeur.id not in profs_intervenants:
                profs_intervenants[c.professeur.id] = {
                    'prof': c.professeur,
                    'role': 'Enseignant',
                    'matieres': c.nom
                }
            else:
                if c.nom not in profs_intervenants[c.professeur.id]['matieres']:
                    profs_intervenants[c.professeur.id]['matieres'] += f", {c.nom}"

    for p in classe.professeurs_assignes.all():
        if p.id not in profs_intervenants:
            profs_intervenants[p.id] = {
                'prof': p,
                'role': 'Enseignant assigné',
                'matieres': p.specialite or 'Général'
            }

    return render_template(
        "class_detail.html",
        classe=classe,
        eleves_details=eleves_details,
        total_eleves=total_eleves,
        capacite=capacite,
        taux_remplissage=taux_remplissage,
        filles_count=filles_count,
        garcons_count=garcons_count,
        total_absences=total_absences,
        absences_justifiees=absences_justifiees,
        absences_non_justifiees=absences_non_justifiees,
        matieres_stats=matieres_stats,
        moyenne_generale_classe=moyenne_generale_classe,
        profs_intervenants=list(profs_intervenants.values())
    )

@main.route("/classes/<int:classe_id>/modifier", methods=["GET", "POST"])
@login_required
@role_required('admin')
def modifier_classe(classe_id):
    classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
    if classe.annee_scolaire and classe.annee_scolaire.statut == "archivee":
        flash("Impossible de modifier une classe d'une annee archivee.", "warning")
        return redirect(url_for("main.liste_classes"))

    form = ClasseForm(obj=classe)
    professeurs = Professeur.query.filter_by(ecole_id=current_user.ecole_id).order_by(Professeur.nom).all()
    form.professeur_principal_id.choices = [(0, "--- Aucun professeur principal ---")] + [
        (p.id, f"{p.prenom} {p.nom}") for p in professeurs
    ]

    if request.method == "GET":
        form.capacite.data = classe.capacite or classe.capacite_max or 35
        form.professeur_principal_id.data = classe.professeur_id or 0
        form.niveau_id.data = classe.niveau_id or 0
        form.section.data = classe.section

    if form.validate_on_submit():
        prof_id = form.professeur_principal_id.data if (form.professeur_principal_id.data and form.professeur_principal_id.data > 0) else None
        capacite_val = form.capacite.data or form.effectif.data or classe.capacite or 35

        classe, error_msg = modifier_classe_depuis_niveau(
            classe=classe,
            ecole_id=current_user.ecole_id,
            niveau_id=form.niveau_id.data,
            nom=form.nom.data,
            section=form.section.data,
            capacite=capacite_val,
            professeur_id=prof_id,
        )
        if error_msg:
            flash(error_msg, "warning")
            return redirect(url_for("main.modifier_classe", classe_id=classe_id))
        flash(f"Classe '{classe.nom}' modifiée avec succès (Capacité : {classe.capacite} élèves) ✅", "success")
        return redirect(url_for("main.liste_classes"))

    return render_template("modifier_classe.html", form=form, classe=classe)


@main.route("/parametres-pedagogiques", methods=["GET", "POST"])
@login_required
@role_required('admin', 'super_admin')
def parametres_pedagogiques():
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        flash("Veuillez selectionner une ecole avant de modifier les parametres pedagogiques.", "warning")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        action = request.form.get("action")
        actif = request.form.get("actif") == "1"

        if action in ("primaire", "secondaire"):
            set_cycle_actif(ecole_id, action, actif)
            flash("Configuration mise a jour.", "success")
        elif action == "niveau":
            niveau_id = request.form.get("niveau_id", type=int)
            _config, error_msg = set_niveau_actif(ecole_id, niveau_id, actif)
            flash(error_msg or "Niveau mis a jour.", "warning" if error_msg else "success")

        return redirect(url_for("main.parametres_pedagogiques"))

    grouped_configs = get_niveau_configs_grouped(ecole_id)
    return render_template("parametres_pedagogiques.html", grouped_configs=grouped_configs)


@main.route("/classes/<int:classe_id>/supprimer", methods=["POST"])
@login_required
@role_required('admin')
def supprimer_classe(classe_id):
    classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
    if classe.annee_scolaire and classe.annee_scolaire.statut == "archivee":
        flash("Impossible de supprimer une classe d'une annee archivee.", "warning")
        return redirect(url_for("main.liste_classes"))

    has_dependencies = bool(
        classe.eleves
        or classe.cours
        or classe.emplois
        or Inscription.query.filter_by(classe_id=classe.id).first()
    )
    if has_dependencies:
        flash("Impossible de supprimer une classe contenant des élèves, cours ou emplois du temps.", "warning")
        return redirect(url_for("main.liste_classes"))

    try:
        db.session.delete(classe)
        db.session.commit()
        flash("Classe supprimée avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression classe {classe_id}: {e}")
        message = "Erreur lors de la suppression de la classe."
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': False, 'message': message}), 500
        flash(message, "danger")

    return redirect(url_for("main.liste_classes"))

@main.route('/get_classes/<int:annee_id>')
@login_required
@role_required('admin', 'professeur')
def get_classes(annee_id):
    from app.models import Classe

    classes_query = Classe.query.filter(
        Classe.ecole_id == current_user.ecole_id,
        Classe.annee_scolaire_id == annee_id
    )
    if current_user.role == 'professeur':
        professeur = current_user.get_professeur()
        if not professeur:
            return jsonify({'classes': []}), 403
        classes_query = classes_query.filter(
            db.or_(
                Classe.professeur_id == professeur.id,
                Classe.id.in_(
                    db.session.query(professeur_classes.c.classe_id)
                    .filter(professeur_classes.c.professeur_id == professeur.id)
                )
            )
        )
    classes = classes_query.order_by(Classe.nom).all()

    classes_list = [{'id': c.id, 'nom': c.nom or c.nom_complet} for c in classes]

    return jsonify({'classes': classes_list})

@main.route('/api/classes/annee/<int:annee_id>')
@login_required
@role_required('admin')
def api_classes_par_annee(annee_id):
    """API pour récupérer les classes d'une année scolaire spécifique"""
    from app.models import Classe

    classes = Classe.query.filter(
        Classe.ecole_id == current_user.ecole_id,
        Classe.annee_scolaire_id == annee_id
    ).order_by(Classe.nom).all()

    classes_list = [{
        'id': c.id,
        'nom': c.nom_complet,
        'niveau': c.niveau,
        'effectif': c.effectif_reel
    } for c in classes]

    return jsonify(classes_list)
