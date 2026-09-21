from . import main
from app.utils_classes import classes_triees_pedagogique
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
from app.services.classes_annuelles import set_classe_ouverte
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
    annee_consultee = get_annee_consultee(ecole_id)

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

    # Support filtre niveau et search
    niveau = request.args.get('niveau') or request.args.get('niveau_id')
    if niveau:
        if str(niveau).isdigit():
            classes_query = classes_query.filter(db.or_(Classe.niveau_id == int(niveau), Classe.niveau == str(niveau)))
        else:
            classes_query = classes_query.filter(Classe.niveau == str(niveau))

    search = (request.args.get('search') or request.args.get('q') or '').strip()
    if search:
        classes_query = classes_query.filter(Classe.nom.ilike(f"%{search}%"))

    classes = classes_triees_pedagogique(classes_query).all()

    return jsonify([{'id': c.id, 'nom': c.nom, 'niveau': c.niveau} for c in classes])


@main.route('/api/niveaux')
@login_required
@role_required('admin', 'professeur')
@ecole_required
def api_niveaux():
    """Retourne la liste des niveaux scolaires actifs pour l'année consultée ou spécifiée (JSON)"""
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        return jsonify([]), 403

    annee_id = request.args.get('annee_id', type=int)
    if annee_id:
        annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
        if not annee:
            return jsonify([]), 403
    else:
        annee = get_annee_consultee(ecole_id)

    if not annee:
        return jsonify([])

    from app.services.structure_annuelle import get_niveaux_annee
    niveaux = get_niveaux_annee(ecole_id, annee.id)
    return jsonify([{
        'id': n.id,
        'nom': n.nom,
        'code': n.code,
        'cycle': n.cycle,
        'ordre': n.ordre
    } for n in niveaux])


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

    annee_consultee = get_annee_consultee(ecole_id)
    base_query = get_classes_annee(ecole_id, annee_consultee.id) if annee_consultee else Classe.query.filter_by(ecole_id=ecole_id).filter(db.false())

    from app.services.structure_annuelle import get_niveaux_annee, niveau_est_dans_structure
    niveaux_annee = get_niveaux_annee(ecole_id, annee_consultee.id) if annee_consultee else []

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
        if str(niveau).isdigit():
            base_query = base_query.filter(db.or_(Classe.niveau_id == int(niveau), Classe.niveau == str(niveau)))
        else:
            base_query = base_query.filter(Classe.niveau == niveau)

    # Appliquer le tri
    if sort_by == 'effectif':
        base_query = base_query.order_by(Classe.effectif.desc())
    elif sort_by == 'niveau':
        base_query = base_query.order_by(Classe.niveau)
    else:  # tri par nom par défaut
        base_query = classes_triees_pedagogique(base_query)

    # Pagination
    classes_paginated = base_query.paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Récupérer toutes les classes pour les statistiques (sans pagination)
    all_classes = base_query.all()
    from app.services.classes_annuelles import precharger_effectifs_classes
    classes_to_precharge = list({c.id: c for c in (all_classes + list(classes_paginated.items))}.values())
    precharger_effectifs_classes(classes_to_precharge, ecole_id, annee_consultee.id if annee_consultee else None)

    # Calculer les valeurs pour la pagination
    start_item = ((page - 1) * per_page) + 1
    end_item = min(page * per_page, classes_paginated.total)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'success': True,
            'total': classes_paginated.total,
            'pages': classes_paginated.pages,
            'page': classes_paginated.page,
            'classes': [
                {
                    'id': c.id,
                    'nom': c.nom,
                    'niveau': c.niveau,
                    'capacite': c.capacite,
                    'effectif_reel': c.effectif_reel,
                    'professeur_principal': f"{c.professeur.prenom} {c.professeur.nom}" if c.professeur else None
                }
                for c in classes_paginated.items
            ]
        })

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
        niveaux_annee=niveaux_annee,
        annee_consultee=annee_consultee,
        start_item=start_item,
        end_item=end_item
    )


@main.route('/classes/generation-rapide', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@ecole_required
def generation_rapide_classes():
    """Génération par lot et accélérée des classes pour l'année scolaire active."""
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for("main.index"))

    annee_consultee = get_annee_consultee(ecole_id)
    if not annee_consultee:
        flash("Veuillez d'abord configurer une année scolaire pour votre établissement.", "warning")
        return redirect(url_for("main.gestion_annees"))

    if annee_consultee.statut == "archivee":
        flash("Impossible de générer des classes dans une année scolaire archivée.", "warning")
        return redirect(url_for("main.liste_classes"))

    from app.services.structure_annuelle import get_niveaux_annee
    from app.services.pedagogie_standard import generer_classes_batch

    niveaux = get_niveaux_annee(ecole_id, annee_consultee.id)

    if request.method == "POST":
        selected_items = request.form.getlist("classes_selected")
        configurations = []
        for item in selected_items:
            if ":" in item:
                parts = item.split(":", 1)
                nid_str = parts[0].strip()
                sec = parts[1].strip().upper()
                if nid_str.isdigit() and sec:
                    configurations.append({
                        "niveau_id": int(nid_str),
                        "section": sec,
                        "capacite": 35
                    })

        if not configurations:
            flash("Veuillez cocher au moins une classe à créer.", "warning")
            return redirect(url_for("main.generation_rapide_classes"))

        classes_creees, classes_existantes, erreur = generer_classes_batch(
            ecole_id=ecole_id,
            annee_id=annee_consultee.id,
            configurations=configurations
        )

        if erreur:
            flash(erreur, "danger")
            return redirect(url_for("main.generation_rapide_classes"))

        if classes_creees:
            msg = f"{len(classes_creees)} classe(s) créée(s) avec succès pour l'année {annee_consultee.nom}."
            if classes_existantes:
                msg += f" ({len(classes_existantes)} classe(s) existai(en)t déjà et ont été ignorée(s))."
            flash(msg, "success")
        else:
            flash(f"Toutes les classes sélectionnées ({len(classes_existantes)}) existent déjà pour cette année.", "info")

        return redirect(url_for("main.liste_classes"))

    # GET
    existing_classes = Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee_consultee.id
    ).all()
    existing_map = {(c.niveau_id, (c.section or "").upper()) for c in existing_classes}

    grouped_niveaux = {"primaire": [], "college": [], "lycee": []}
    for n in niveaux:
        if n.cycle == "lycee" and (n.code or "").upper() in ("1ERE", "TERMINALE"):
            sections = ["A", "C", "D"]
            libelle = "Série"
        elif n.cycle == "lycee" and (n.code or "").upper() in ("2NDE", "SECONDE"):
            sections = ["A", "B", "C", "S"]
            libelle = "Section / Série"
        else:
            sections = ["A", "B", "C", "D"]
            libelle = "Section"

        grouped_niveaux.setdefault(n.cycle or "college", []).append({
            "niveau": n,
            "sections": sections,
            "libelle": libelle,
            "existing_sections": [sec for (nid, sec) in existing_map if nid == n.id]
        })

    return render_template(
        "classes_generation_rapide.html",
        grouped_niveaux=grouped_niveaux,
        annee_consultee=annee_consultee,
        total_niveaux=len(niveaux)
    )


@main.route("/classes/add", methods=["GET", "POST"])
@login_required
@role_required('admin')
def ajouter_classe():
    from app.models import AnneeScolaire, Professeur, Classe, NiveauScolaire

    # Résolution de l'année cible : paramètre explicite > année consultée > année active > dernière année non archivée
    annee_cible = get_annee_consultee(current_user.ecole_id)

    if annee_cible and annee_cible.statut == "archivee":
        flash("Impossible de creer une classe dans une annee archivee.", "warning")
        return redirect(url_for("main.liste_classes"))

    if not annee_cible:
        flash("Veuillez d'abord configurer une année scolaire pour votre établissement avant d'ajouter une classe.", "warning")
        return redirect(url_for("main.gestion_annees"))

    selected_niveau_id = request.args.get("niveau_id", type=int)
    form = ClasseForm()
    # Professeurs filtrés par école
    professeurs = get_ecole_filter_query(Professeur).filter_by(ecole_id=current_user.ecole_id).order_by(Professeur.nom).all()
    form.professeur_principal_id.choices = [(0, "--- Aucun professeur principal ---")] + [
        (p.id, f"{p.prenom} {p.nom}") for p in professeurs
    ]
    form.annee_scolaire_id.choices = [(annee_cible.id, annee_cible.nom)]
    form.annee_scolaire_id.data = annee_cible.id

    # Filtrer les niveaux avec les niveaux annuels actifs de l'année cible
    from app.services.niveaux_annuels import get_niveaux_annuels_actifs
    niveaux_annee = get_niveaux_annuels_actifs(current_user.ecole_id, annee_cible.id)
    form.niveau_id.choices = [(n.id, n.nom) for n in niveaux_annee]
    form.niveau.choices = [(n.nom, n.nom) for n in niveaux_annee]

    if request.method == "GET":
        if selected_niveau_id and any(choice_id == selected_niveau_id for choice_id, _label in form.niveau_id.choices):
            form.niveau_id.data = selected_niveau_id
        elif not form.niveau_id.data and form.niveau_id.choices:
            form.niveau_id.data = form.niveau_id.choices[0][0]

    # Garde-fou 3 : Calculer niveau et nom côté backend si non renseignés dans le payload UI
    niveau_obj = db.session.get(NiveauScolaire, form.niveau_id.data) if form.niveau_id.data else None
    if niveau_obj:
        if not form.niveau.data:
            form.niveau.data = niveau_obj.nom
    if not form.nom.data:
        form.nom.data = "AUTO"

    if form.validate_on_submit():
        try:
            from app.services.structure_annuelle import niveau_est_dans_structure
            if not niveau_est_dans_structure(current_user.ecole_id, annee_cible.id, form.niveau_id.data):
                flash("Le niveau sélectionné n'est pas configuré pour cette année scolaire.", "danger")
                return redirect(url_for("main.ajouter_classe"))

            prof_id = form.professeur_principal_id.data if (form.professeur_principal_id.data and form.professeur_principal_id.data > 0) else None
            capacite_val = form.capacite.data or form.effectif.data or 35
            classe, error_msg = creer_classe_depuis_niveau(
                ecole_id=current_user.ecole_id,
                annee_scolaire_id=annee_cible.id,
                niveau_id=form.niveau_id.data,
                nom=None,
                section=form.section.data,
                capacite=capacite_val,
                professeur_id=prof_id,
            )
            if error_msg:
                flash(error_msg, "warning")
                return redirect(url_for("main.ajouter_classe", niveau_id=form.niveau_id.data))
            flash(f"Classe '{classe.nom}' ajoutée avec succès pour l'année {annee_cible.nom}.", "success")
            return redirect(url_for("main.liste_classes"))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur ajout classe : {e}")
            flash("Erreur lors de l'ajout de la classe.", "danger")
            return redirect(url_for("main.ajouter_classe", niveau_id=form.niveau_id.data))

    niveaux_form = []
    for niveau_id, label in form.niveau_id.choices:
        niveau = db.session.get(NiveauScolaire, niveau_id)
        if niveau:
            niveaux_form.append({"id": niveau.id, "nom": label, "cycle": niveau.cycle})

    return render_template(
        "add_class.html",
        form=form,
        professeurs=professeurs,
        annee_active=annee_cible,
        niveaux_form=niveaux_form,
        selected_niveau_id=selected_niveau_id,
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
    from app.models import Absence, Note, Cours, Eleve, Professeur, Inscription

    # 1. Liste des élèves réels de la classe
    inscriptions = (
        Inscription.query
        .filter_by(classe_id=classe.id, ecole_id=classe.ecole_id, annee_scolaire_id=classe.annee_scolaire_id)
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )
    eleves = [inscription.eleve for inscription in inscriptions if inscription.eleve]
    inscription_ids = [inscription.id for inscription in inscriptions]
    total_eleves = len(eleves)
    capacite = classe.capacite or classe.capacite_max or 35
    taux_remplissage = round((total_eleves / capacite) * 100) if capacite > 0 else 0

    # 2. Répartition réelle par genre
    filles_count = sum(1 for e in eleves if (e.genre or '').upper() == 'F')
    garcons_count = sum(1 for e in eleves if (e.genre or '').upper() in ['M', 'H', 'G'])

    # 3. Statistiques réelles des absences
    eleve_ids = [e.id for e in eleves]
    total_absences = Absence.query.filter(Absence.inscription_id.in_(inscription_ids)).count() if inscription_ids else 0
    absences_justifiees = Absence.query.filter(Absence.inscription_id.in_(inscription_ids), Absence.justifiee == True).count() if inscription_ids else 0
    absences_non_justifiees = total_absences - absences_justifiees

    from app.services.evaluations import calculer_stats_et_classements_classe
    stats = calculer_stats_et_classements_classe(current_user.ecole_id, classe.id, classe.annee_scolaire_id)

    # 4. Données détaillées par élève
    eleves_details = []
    for e in eleves:
        inscription = next((i for i in inscriptions if i.eleve_id == e.id), None)
        nb_abs = Absence.query.filter_by(inscription_id=inscription.id).count() if inscription else 0

        moyenne_e = None
        eval_status = "non_evalue"
        if inscription:
            ev = stats.get("evals_by_ins", {}).get(inscription.id)
            if not ev:
                from app.services.evaluations import calculer_completude_inscription
                ev = calculer_completude_inscription(current_user.ecole_id, classe.annee_scolaire_id, inscription)
            if ev:
                moyenne_e = ev.get("average")
                eval_status = ev.get("status", "non_evalue")

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
            'moyenne': moyenne_e,
            'eval_status': eval_status
        })

    # 5. Moyennes réelles par matière
    cours_classe = Cours.query.filter_by(classe_id=classe.id).all()
    matieres_stats = []
    for c in cours_classe:
        notes_cours = Note.query.filter(Note.cours_id == c.id, Note.inscription_id.in_(inscription_ids)).all() if inscription_ids else []
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
    moyenne_generale_classe = stats["moyenne_classe_officielle"]

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

    from app.services.classes_annuelles import precharger_effectif_classe
    precharger_effectif_classe(classe)
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
@main.route("/classe/<int:classe_id>/supprimer", methods=["POST"])
@login_required
@role_required('admin')
def supprimer_classe(classe_id):
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.is_json
        or request.accept_mimetypes.best == 'application/json'
    )
    classe = Classe.query.get(classe_id)
    if not classe:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Classe introuvable.'}), 404
        abort(404)

    # 🛡️ Contrôle strict multi-tenant
    if classe.ecole_id != current_user.ecole_id:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Action non autorisée.'}), 403
        abort(403)

    if classe.annee_scolaire and classe.annee_scolaire.statut == "archivee":
        msg = "Impossible de supprimer une classe d'une année archivée."
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, "warning")
        return redirect(url_for("main.liste_classes"))

    has_inscriptions = Inscription.query.filter_by(classe_id=classe.id).first() is not None
    has_cours = Cours.query.filter_by(classe_id=classe.id).first() is not None
    has_emplois = bool(classe.emplois)

    if has_inscriptions or has_cours or has_emplois:
        msg = "Impossible de supprimer une classe contenant des élèves ou des cours associés."
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, "warning")
        return redirect(url_for("main.liste_classes"))

    try:
        deleted_id = classe.id
        db.session.delete(classe)
        db.session.commit()
        if is_ajax:
            return jsonify({'success': True, 'message': 'Classe supprimée avec succès.', 'deleted_id': deleted_id})
        flash("Classe supprimée avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression classe {classe_id}: {e}")
        message = "Suppression impossible. Veuillez réessayer."
        if is_ajax:
            return jsonify({'success': False, 'message': message}), 500
        flash(message, "danger")

    return redirect(url_for("main.liste_classes"))


@main.route("/classes/<int:classe_id>/statut", methods=["POST"])
@login_required
@role_required('admin', 'super_admin')
def changer_statut_classe(classe_id):
    ecole_id = current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')
    if not ecole_id:
        message = "Veuillez selectionner une ecole."
        if request.is_json:
            return jsonify({"success": False, "message": message}), 403
        flash(message, "warning")
        return redirect(url_for("main.liste_classes"))

    payload = request.get_json(silent=True) or {}
    statut = payload.get("statut") or request.form.get("statut")
    if statut not in ("ouverte", "fermee"):
        message = "Statut de classe invalide."
        if request.is_json:
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(url_for("main.liste_classes"))

    classe, error = set_classe_ouverte(ecole_id, classe_id, statut == "ouverte")
    if error:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "message": error}), 400
        flash(error, "warning")
        return redirect(url_for("main.liste_classes"))

    db.session.commit()
    message = "Classe ouverte." if classe.statut == "ouverte" else "Classe fermee."
    if request.is_json:
        return jsonify({"success": True, "message": message, "classe_id": classe.id, "statut": classe.statut})
    flash(message, "success")
    return redirect(request.referrer or url_for("main.liste_classes"))

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
    classes = classes_triees_pedagogique(classes_query).all()

    classes_list = [{'id': c.id, 'nom': c.nom or c.nom_complet} for c in classes]

    return jsonify({'classes': classes_list})

@main.route('/api/classes/annee/<int:annee_id>')
@login_required
@role_required('admin')
def api_classes_par_annee(annee_id):
    from app.models import Classe
    from app.services.classes_annuelles import precharger_effectifs_classes

    classes = classes_triees_pedagogique(Classe.query.filter(
        Classe.ecole_id == current_user.ecole_id,
        Classe.annee_scolaire_id == annee_id
    )).all()

    precharger_effectifs_classes(classes, current_user.ecole_id, annee_id)

    classes_list = [{
        'id': c.id,
        'nom': c.nom_complet,
        'niveau': c.niveau,
        'effectif': c.effectif_reel
    } for c in classes]

    return jsonify(classes_list)
