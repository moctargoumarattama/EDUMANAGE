from . import main
from .common import (
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


@main.route('/api/classes')
@login_required
@role_required('admin', 'enseignant')
@ecole_required
def api_classes():
    """Retourne la liste des classes filtrÃ©e par Ã©cole et annÃ©e active (JSON)"""
    # DÃ©termination de l'Ã©cole
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        return jsonify([]), 403  # Super-admin sans Ã©cole sÃ©lectionnÃ©e

    # RÃ©cupÃ©ration de l'annÃ©e scolaire active
    annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()

    # Filtrage des classes
    classes_query = Classe.query.filter_by(ecole_id=ecole_id)
    if current_user.role in ('enseignant', 'professeur'):
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
    if annee_active:
        classes_query = classes_query.filter_by(annee_scolaire_id=annee_active.id)
    classes = classes_query.order_by(Classe.nom).all()

    return jsonify([{'id': c.id, 'nom': c.nom} for c in classes])

@main.route("/classes")
@login_required
def liste_classes():
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Nombre d'Ã©lÃ©ments par page
    
    # Filtres
    search = request.args.get('search', '')
    niveau = request.args.get('niveau', '')
    sort_by = request.args.get('sort', 'nom')
    
    # Base query pour l'Ã©cole de l'utilisateur
    base_query = Classe.query.filter_by(ecole_id=current_user.ecole_id)
    
    if current_user.role in ('professeur', 'enseignant'):
        professeur = current_user.get_professeur()
        if professeur:
            base_query = base_query.join(Classe.professeurs_assignes).filter(Professeur.id == professeur.id)
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
    else:  # tri par nom par dÃ©faut
        base_query = base_query.order_by(Classe.nom)
    
    # Pagination
    classes_paginated = base_query.paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    # RÃ©cupÃ©rer toutes les classes pour les statistiques (sans pagination)
    all_classes = base_query.all()
    
    # Calculer les valeurs pour la pagination
    start_item = ((page - 1) * per_page) + 1
    end_item = min(page * per_page, classes_paginated.total)
    
    return render_template(
        "classes.html",
        classes=classes_paginated.items,
        pagination=classes_paginated,
        total_eleves=sum(c.effectif for c in all_classes),
        moyenne_effectif=int(sum(c.effectif for c in all_classes) / len(all_classes)) if all_classes else 0,
        classes_pleines=sum(1 for c in all_classes if c.effectif >= (c.capacite or 30)),
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
    form = ClasseForm()
    # Professeurs filtrÃ©s par Ã©cole
    professeurs = get_ecole_filter_query(Professeur).filter_by(ecole_id=current_user.ecole_id).all()

    # Charger les annÃ©es scolaires pour le form
    from app.models import AnneeScolaire
    annees_ecole = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).order_by(AnneeScolaire.id.desc()).all()
    form.annee_scolaire_id.choices = [(a.id, a.nom) for a in annees_ecole]

    # PrÃ©-selection annÃ©e active
    annee_active = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id, statut='active').first()
    if annee_active:
        form.annee_scolaire_id.data = annee_active.id

    if form.validate_on_submit():
        try:
            # VÃ©rifier doublon : mÃªme nom + annÃ©e + Ã©cole
            existing = Classe.query.filter_by(
                nom=form.nom.data,
                annee_scolaire_id=form.annee_scolaire_id.data,
                ecole_id=current_user.ecole_id
            ).first()
            if existing:
                flash("Une classe avec ce nom existe dÃ©jÃ  pour cette annÃ©e scolaire.", "warning")
                return redirect(url_for("main.ajouter_classe"))

            classe = Classe(
                nom=form.nom.data,
                niveau=form.niveau.data,
                effectif=form.effectif.data,
                salle=form.salle.data,
                professeur_id=form.professeur_principal_id.data,
                annee_scolaire_id=form.annee_scolaire_id.data,
                ecole_id=current_user.ecole_id
            )
            db.session.add(classe)
            db.session.commit()
            flash("Classe ajoutÃ©e avec succÃ¨s Ã¢Å“â€¦", "success")
            return redirect(url_for("main.liste_classes"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur ajout classe : {e}")
            flash("Erreur lors de l'ajout de la classe.", "danger")
            return redirect(url_for("main.ajouter_classe"))

    return render_template("add_class.html", form=form, professeurs=professeurs, annees_ecole=annees_ecole, annee_active=annee_active)

@main.route("/classes/<int:classe_id>")
@login_required
@role_required('admin', 'enseignant', 'professeur')
def detail_classe(classe_id):
    if current_user.role in ('professeur', 'enseignant'):
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
            flash("Acces non autorise a cette classe.", "danger")
            return redirect(url_for('main.liste_classes'))
    else:
        classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
    # RÃ©cupÃ©rer inscriptions et notes avec optimisation N+1
    inscriptions = Inscription.query.options(
        joinedload(Inscription.eleve).joinedload(Eleve.notes).joinedload(Note.cours)
    ).filter_by(classe_id=classe.id).all()

    eleves_data = []
    for ins in inscriptions:
        eleve = ins.eleve
        notes_par_annee = {}
        for n in eleve.notes:
            annee = ins.annee_scolaire
            if annee not in notes_par_annee:
                notes_par_annee[annee] = []
            notes_par_annee[annee].append({
                "matiere": n.cours.nom if n.cours else "N/A",
                "valeur": n.valeur,
                "periode": n.periode
            })

        eleves_data.append({
            "nom": eleve.nom,
            "prenom": eleve.prenom,
            "classe": classe.nom,
            "parent": f"{eleve.parent.nom} {eleve.parent.prenom}" if eleve.parent else "N/A",
            "annee_premiere_ecole": getattr(eleve, 'annee_premiere_ecole', "N/A"),
            "notes_par_annee": notes_par_annee
        })

    return render_template(
        "class_detail.html",
        classe=classe,
        eleves_data=eleves_data
    )

@main.route("/classes/<int:classe_id>/modifier", methods=["GET", "POST"])
@login_required
@role_required('admin')
def modifier_classe(classe_id):
    classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
    form = ClasseForm(obj=classe)
    annees_ecole = AnneeScolaire.query.filter_by(ecole_id=current_user.ecole_id).order_by(AnneeScolaire.id.desc()).all()
    professeurs = Professeur.query.filter_by(ecole_id=current_user.ecole_id).order_by(Professeur.nom).all()
    form.annee_scolaire_id.choices = [(a.id, a.nom) for a in annees_ecole]
    form.professeur_principal_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in professeurs]

    if request.method == "GET":
        form.annee_scolaire_id.data = classe.annee_scolaire_id
        form.professeur_principal_id.data = classe.professeur_id

    if form.validate_on_submit():
        annee = AnneeScolaire.query.filter_by(id=form.annee_scolaire_id.data, ecole_id=current_user.ecole_id).first()
        professeur = None
        if form.professeur_principal_id.data:
            professeur = Professeur.query.filter_by(id=form.professeur_principal_id.data, ecole_id=current_user.ecole_id).first()

        if not annee:
            flash("AnnÃ©e scolaire invalide pour cette Ã©cole.", "danger")
            return redirect(url_for("main.modifier_classe", classe_id=classe.id))
        if form.professeur_principal_id.data and not professeur:
            flash("Professeur invalide pour cette Ã©cole.", "danger")
            return redirect(url_for("main.modifier_classe", classe_id=classe.id))

        classe.nom = form.nom.data
        classe.niveau = form.niveau.data
        classe.effectif = form.effectif.data
        classe.salle = form.salle.data
        classe.professeur_id = professeur.id if professeur else None
        classe.annee_scolaire_id = annee.id
        db.session.commit()
        flash("Classe modifiÃ©e avec succÃ¨s.", "success")
        return redirect(url_for("main.liste_classes"))

    return render_template("modifier_classe.html", form=form, classe=classe, annees_ecole=annees_ecole)


@main.route("/classes/<int:classe_id>/supprimer", methods=["POST"])
@login_required
@role_required('admin')
def supprimer_classe(classe_id):
    classe = Classe.query.filter_by(id=classe_id, ecole_id=current_user.ecole_id).first_or_404()
    has_dependencies = bool(
        classe.eleves
        or classe.cours
        or classe.emplois
        or Inscription.query.filter_by(classe_id=classe.id).first()
    )
    if has_dependencies:
        flash("Impossible de supprimer une classe contenant des Ã©lÃ¨ves, cours ou emplois du temps.", "warning")
        return redirect(url_for("main.liste_classes"))

    try:
        db.session.delete(classe)
        db.session.commit()
        flash("Classe supprimÃ©e avec succÃ¨s.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression classe {classe_id}: {e}")
        flash("Erreur lors de la suppression de la classe.", "danger")

    return redirect(url_for("main.liste_classes"))

@main.route('/get_classes/<int:annee_id>')
@login_required
@role_required('admin', 'enseignant')
def get_classes(annee_id):
    from app.models import Classe

    classes_query = Classe.query.filter(
        Classe.ecole_id == current_user.ecole_id,
        Classe.annee_scolaire_id == annee_id
    )
    if current_user.role in ('enseignant', 'professeur'):
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
    """API pour rÃ©cupÃ©rer les classes d'une annÃ©e scolaire spÃ©cifique"""
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

