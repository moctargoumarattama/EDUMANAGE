from . import main
from .common import (
    AnneeScolaire,
    CSRFForm,
    Ecole,
    current_app,
    current_user,
    datetime,
    db,
    flash,
    get_ecole_filter_query,
    jsonify,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    session,
    url_for,
)
from app.services.classes_annuelles import preparer_structure_annee, classe_est_ouverte
from app.services.annees_scolaires import (
    get_annee_consultee,
    set_annee_consultee,
    construire_nom_annee,
    valider_dates_annee,
    valider_unicite_annee,
    MESSAGE_ANNEE_ARCHIVEE_MODIF,
)
from app.services.structure_annuelle import get_niveaux_candidats_annuels
from app.utils_classes import classes_triees_pedagogique
from app.models import Classe, Cours, Eleve, Inscription, PeriodeBulletin
from app.services.niveaux_annuels import (
    get_selection_annuelle,
    sauvegarder_selection_annuelle,
    get_niveaux_annuels_actifs,
)
from app.services.inscriptions_annuelles import get_inscription
from app.services.passage_annee import (
    valider_contexte_passage,
    preparer_passage_eleve,
    executer_passage_eleve,
    preparer_passage_masse,
    executer_passage_masse,
    get_moyennes_annuelles_eleves,
    reinscrire_ancien_eleve,
    annuler_decision_passage,
)


from app.services.preparation_annee import (
    determiner_source_passage_pour_cible,
    get_etat_preparation_annee,
)
from app.services.activation_annee import (
    preparer_activation_annee,
    activer_annee_scolaire,
)
from app.services.semestres import calendrier_configure, configurer_semestres_annee, get_semestres_annee
from app.services.duplication_structure import dupliquer_structure_annee


def _current_ecole_id_for_annees():
    return current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')


def _annee_saisie_depuis_form():
    debut = request.form.get('annee_scolaire', '').strip()
    if not debut:
        debut = request.form.get('annee_debut_court', '').strip()
    if not debut:
        date_debut_str = request.form.get('date_debut', '').strip()
        if date_debut_str and len(date_debut_str) >= 4:
            debut = date_debut_str[:4]
    return debut


@main.route('/annees', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def gestion_annees():
    csrf_form = CSRFForm()

    # 🔹 Récupération des écoles accessibles
    if current_user.role == 'super_admin':
        ecoles = get_ecole_filter_query(Ecole).all()
        # Super admin peut tout voir
        annees = AnneeScolaire.query.options(
            db.selectinload(AnneeScolaire.ecole)
        ).order_by(AnneeScolaire.date_debut.desc()).all()
    else:
        # Si un admin gère une seule école
        ecoles = [current_user.ecole]
        # ⚠️ Si un admin gère plusieurs écoles, décommente :
        # ecoles = current_user.ecoles_gerees
        ecole_ids = [e.id for e in ecoles]

        annees = AnneeScolaire.query.options(
            db.selectinload(AnneeScolaire.ecole)
        ).filter(AnneeScolaire.ecole_id.in_(ecole_ids)).order_by(
            AnneeScolaire.date_debut.desc()
        ).all()

    if request.method == 'POST' and csrf_form.validate_on_submit():
        action = request.form.get('action')
        annee_id = request.form.get('annee_id')

        if action == 'activer' and annee_id:
            annee = db.session.get(AnneeScolaire, int(annee_id))
            if annee and annee.ecole_id in [e.id for e in ecoles]:
                if not calendrier_configure(annee.ecole_id, annee.id):
                    flash("Configurez les semestres S1 et S2 avant d'activer cette année.", "danger")
                    return redirect(url_for('main.gestion_annees'))
                return redirect(url_for('main.activation_annee_confirmation', annee_id=annee.id))
            else:
                flash("Action non autorisée pour cette école.", "danger")

        elif action == 'ajouter':
            annee_court = _annee_saisie_depuis_form()
            date_debut_str = request.form.get('date_debut')
            date_fin_str = request.form.get('date_fin')
            ecole_id = request.form.get('ecole_id')

            try:
                # ✅ Sécurisation : si l'admin n’a qu’une seule école, on force automatiquement
                if not ecole_id and len(ecoles) == 1:
                    ecole_id = ecoles[0].id

                nom, debut_annee, fin_annee, err_nom = construire_nom_annee(annee_court)
                if err_nom:
                    flash(err_nom, "danger")
                    return redirect(url_for('main.gestion_annees'))

                if not (date_debut_str and date_fin_str and ecole_id):
                    flash("Tous les champs sont obligatoires.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                ecole_id = int(ecole_id)
                if ecole_id not in [e.id for e in ecoles]:
                    flash("Vous ne pouvez pas créer une année pour cette école.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                date_debut = datetime.strptime(date_debut_str, "%Y-%m-%d").date()
                date_fin = datetime.strptime(date_fin_str, "%Y-%m-%d").date()

                ok_dates, err_dates = valider_dates_annee(date_debut, date_fin, debut_annee, fin_annee)
                if not ok_dates:
                    flash(err_dates, "danger")
                    return redirect(url_for('main.gestion_annees'))

                ok_unicite, err_unicite = valider_unicite_annee(ecole_id, nom)
                if not ok_unicite:
                    flash(err_unicite, "danger")
                    return redirect(url_for('main.gestion_annees'))

                nouvelle_annee = AnneeScolaire(
                    nom=nom,
                    date_debut=date_debut,
                    date_fin=date_fin,
                    statut='planifiee',
                    ecole_id=ecole_id
                )
                db.session.add(nouvelle_annee)
                db.session.commit()
                flash(f"Nouvelle année {nom} ajoutée.", "success")

            except ValueError:
                flash("Format de date invalide (AAAA-MM-JJ).", "danger")
                return redirect(url_for('main.gestion_annees'))
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception(f"Erreur lors de l'ajout de l'année : {e}")
                flash("Une erreur est survenue lors de l'ajout de l'année.", "danger")

        elif action == 'modifier':
            annee_id = request.form.get('annee_id')
            annee_court = _annee_saisie_depuis_form()
            date_debut_str = request.form.get('date_debut')
            date_fin_str = request.form.get('date_fin')

            if not annee_id:
                flash("Année non spécifiée.", "danger")
                return redirect(url_for('main.gestion_annees'))

            try:
                annee_id = int(annee_id)
            except (ValueError, TypeError):
                flash("Identifiant d'année invalide.", "danger")
                return redirect(url_for('main.gestion_annees'))

            annee = db.session.get(AnneeScolaire, annee_id)
            if not annee or annee.ecole_id not in [e.id for e in ecoles]:
                flash("Année introuvable ou accès non autorisé.", "danger")
                return redirect(url_for('main.gestion_annees'))

            if annee.statut == 'archivee':
                flash(MESSAGE_ANNEE_ARCHIVEE_MODIF, "warning")
                return redirect(url_for('main.gestion_annees'))

            nom, debut_annee, fin_annee, err_nom = construire_nom_annee(annee_court)
            if err_nom:
                flash(err_nom, "danger")
                return redirect(url_for('main.gestion_annees'))

            if not (date_debut_str and date_fin_str):
                flash("Tous les champs sont obligatoires.", "danger")
                return redirect(url_for('main.gestion_annees'))

            try:
                date_debut = datetime.strptime(date_debut_str, "%Y-%m-%d").date()
                date_fin = datetime.strptime(date_fin_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Format de date invalide (AAAA-MM-JJ).", "danger")
                return redirect(url_for('main.gestion_annees'))

            ok_dates, err_dates = valider_dates_annee(date_debut, date_fin, debut_annee, fin_annee)
            if not ok_dates:
                flash(err_dates, "danger")
                return redirect(url_for('main.gestion_annees'))

            ok_unicite, err_unicite = valider_unicite_annee(annee.ecole_id, nom, annee_id_exclure=annee.id)
            if not ok_unicite:
                flash(err_unicite, "danger")
                return redirect(url_for('main.gestion_annees'))

            try:
                annee.nom = nom
                annee.date_debut = date_debut
                annee.date_fin = date_fin
                db.session.commit()
                flash(f"L'année scolaire {nom} a été modifiée avec succès.", "success")
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception(f"Erreur lors de la modification de l'année : {e}")
                flash("Une erreur est survenue lors de la modification de l'année.", "danger")

        from app.utils import get_school_setup_state
        if current_user.role == 'admin' and current_user.ecole_id and not get_school_setup_state(current_user.ecole_id)['setup_complete']:
            return redirect(url_for('main.onboarding'))

        return redirect(url_for('main.gestion_annees'))

    ecole_id = _current_ecole_id_for_annees()
    annee_consultee = get_annee_consultee(ecole_id) if ecole_id else None
    source_active_par_ecole = {
        a.ecole_id: a for a in AnneeScolaire.query.filter_by(statut='active').all()
    }
    sources_passage_par_cible = {}
    for a in annees:
        src = determiner_source_passage_pour_cible(a, annees)
        if src:
            sources_passage_par_cible[a.id] = src
    semestres_par_annee = {a.id: get_semestres_annee(a.ecole_id, a.id) for a in annees}

    return render_template(
        'gestion_annees.html',
        annees=annees,
        ecoles=ecoles,
        csrf_form=csrf_form,
        annee_consultee=annee_consultee,
        source_active_par_ecole=source_active_par_ecole,
        sources_passage_par_cible=sources_passage_par_cible,
        semestres_par_annee=semestres_par_annee,
    )


@main.route('/annees/<int:annee_id>/semestres', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def configurer_semestres(annee_id):
    csrf_form = CSRFForm()
    if not csrf_form.validate_on_submit():
        flash("Session expirée ou jeton CSRF invalide.", "danger")
        return redirect(url_for('main.gestion_annees'))

    if current_user.role == 'super_admin':
        ecoles = get_ecole_filter_query(Ecole).all()
    else:
        ecoles = [current_user.ecole]

    annee = AnneeScolaire.query.get_or_404(annee_id)
    if annee.ecole_id not in [e.id for e in ecoles]:
        flash("Action non autorisée pour cette école.", "danger")
        return redirect(url_for('main.gestion_annees'))

    fin_semestre_1_str = request.form.get('fin_semestre_1')
    try:
        fin_semestre_1 = datetime.strptime(fin_semestre_1_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        flash("Date de fin du Semestre 1 invalide.", "danger")
        return redirect(url_for('main.gestion_annees'))

    _periodes, err = configurer_semestres_annee(annee.ecole_id, annee.id, fin_semestre_1)
    if err:
        flash(err, "danger")
    else:
        flash("Calendrier des semestres enregistré.", "success")
    return redirect(url_for('main.gestion_annees'))


@main.route('/annees/<int:annee_id>/modifier', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def modifier_annee(annee_id):
    csrf_form = CSRFForm()
    if not csrf_form.validate_on_submit():
        flash("Session expirée ou jeton CSRF invalide.", "danger")
        return redirect(url_for('main.gestion_annees'))

    if current_user.role == 'super_admin':
        ecoles = get_ecole_filter_query(Ecole).all()
    else:
        ecoles = [current_user.ecole]

    annee = AnneeScolaire.query.get_or_404(annee_id)
    if annee.ecole_id not in [e.id for e in ecoles]:
        flash("Action non autorisée pour cette école.", "danger")
        return redirect(url_for('main.gestion_annees'))

    if annee.statut == 'archivee':
        flash(MESSAGE_ANNEE_ARCHIVEE_MODIF, "warning")
        return redirect(url_for('main.gestion_annees'))

    annee_court = _annee_saisie_depuis_form()
    date_debut_str = request.form.get('date_debut')
    date_fin_str = request.form.get('date_fin')

    nom, debut_annee, fin_annee, err_nom = construire_nom_annee(annee_court)
    if err_nom:
        flash(err_nom, "danger")
        return redirect(url_for('main.gestion_annees'))

    if not (date_debut_str and date_fin_str):
        flash("Tous les champs sont obligatoires.", "danger")
        return redirect(url_for('main.gestion_annees'))

    try:
        date_debut = datetime.strptime(date_debut_str, "%Y-%m-%d").date()
        date_fin = datetime.strptime(date_fin_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Format de date invalide (AAAA-MM-JJ).", "danger")
        return redirect(url_for('main.gestion_annees'))

    ok_dates, err_dates = valider_dates_annee(date_debut, date_fin, debut_annee, fin_annee)
    if not ok_dates:
        flash(err_dates, "danger")
        return redirect(url_for('main.gestion_annees'))

    ok_unicite, err_unicite = valider_unicite_annee(annee.ecole_id, nom, annee_id_exclure=annee.id)
    if not ok_unicite:
        flash(err_unicite, "danger")
        return redirect(url_for('main.gestion_annees'))

    try:
        annee.nom = nom
        annee.date_debut = date_debut
        annee.date_fin = date_fin
        db.session.commit()
        flash(f"L'année scolaire {nom} a été modifiée avec succès.", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f"Erreur lors de la modification de l'année : {e}")
        flash("Une erreur est survenue lors de la modification de l'année.", "danger")

    return redirect(url_for('main.gestion_annees'))


@main.route('/annees/<int:annee_id>/consulter', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def consulter_annee(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez selectionner une ecole.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee = set_annee_consultee(ecole_id, annee_id)
    if not annee:
        flash("Annee invalide pour cet etablissement.", "danger")
        return redirect(url_for('main.gestion_annees'))

    flash(f"Annee consultee : {annee.nom}.", "success")
    next_url = request.form.get("next") or request.args.get("next")
    if next_url and next_url.startswith('/') and not next_url.startswith('//') and '\\' not in next_url:
        return redirect(next_url)
    return redirect(url_for('main.gestion_annees'))

@main.route('/changer_annee/<int:annee_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def changer_annee(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first_or_404()
    return redirect(url_for('main.activation_annee_confirmation', annee_id=annee.id))


@main.route('/annees/<int:annee_id>/preparer-structure', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def preparer_structure_annee_route(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        return jsonify({"success": False, "message": "Veuillez selectionner une ecole."}), 403

    payload = request.get_json(silent=True) or {}
    source_id = payload.get('annee_source_id') or request.form.get('annee_source_id', type=int)
    redirect_to_structure = request.form.get('_redirect_to_structure') == '1'
    try:
        source_id = int(source_id) if source_id else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Annee source invalide."}), 400

    result, error = preparer_structure_annee(ecole_id, annee_id, source_id)
    if error:
        db.session.rollback()
        if redirect_to_structure:
            flash(error, "warning")
            return redirect(url_for('main.structure_annee', annee_id=annee_id))
        return jsonify({"success": False, "message": error}), 400
    db.session.commit()

    if redirect_to_structure:
        flash(
            f"{result['classes_creees']} classe(s) et {result['cours_crees']} cours prepare(s).",
            "success"
        )
        return redirect(url_for('main.structure_annee', annee_id=annee_id))

    return jsonify({
        "success": True,
        "message": "Structure annuelle preparee.",
        "result": {
            "annee_source_id": result["annee_source_id"],
            "annee_cible_id": result["annee_cible_id"],
            "classes_creees": result["classes_creees"],
            "classes_existantes": result["classes_existantes"],
            "classes_ignorees_niveau_desactive": result["classes_ignorees_niveau_desactive"],
            "classes_fermees": result["classes_fermees"],
            "cours_crees": result["cours_crees"],
            "cours_existants": result["cours_existants"],
            "cours_ignores_classe_fermee": result["cours_ignores_classe_fermee"],
        }
    })


@main.route('/annees/<int:annee_id>/preparation', methods=['GET', 'POST'], endpoint='preparation_annee')
@login_required
@role_required('admin', 'super_admin')
def preparation_annee(annee_id):
    return onboarding_rentree(cible_id=annee_id)



@main.route('/annees/<int:annee_id>/activation', methods=['GET'], endpoint='activation_annee_confirmation')
@login_required
@role_required('admin', 'super_admin')
def activation_annee_confirmation(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        flash("Année scolaire introuvable.", "danger")
        return redirect(url_for('main.gestion_annees'))

    if annee.statut == 'active':
        flash("Cette année scolaire est déjà active.", "info")
        return redirect(url_for('main.gestion_annees'))

    if annee.statut == 'archivee':
        flash("Cette année scolaire est archivée et ne peut plus être activée.", "warning")
        return redirect(url_for('main.gestion_annees'))

    prep, err = preparer_activation_annee(ecole_id, annee.id)
    if err:
        flash(err, "danger")
        if "semestres" in err.lower():
            return redirect(url_for('main.gestion_annees'))
        return redirect(url_for('main.preparation_annee', annee_id=annee.id))

    csrf_form = CSRFForm()
    return render_template(
        'activation_annee_confirmation.html',
        annee=prep['annee_cible'],
        ancienne_active=prep['ancienne_active'],
        etat_preparation=prep['etat_preparation'],
        nb_synchronises=prep['nb_synchronises'],
        nb_sans_inscription=prep['nb_sans_inscription'],
        nb_promus=prep.get('nb_promus', 0),
        nb_redoublants=prep.get('nb_redoublants', 0),
        nb_nouveaux=prep.get('nb_nouveaux', 0),
        prep=prep,
        csrf_form=csrf_form,
    )


@main.route('/annees/<int:annee_id>/activer', methods=['POST'], endpoint='activer_annee')
@login_required
@role_required('admin', 'super_admin')
def activer_annee(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        flash("Année scolaire introuvable.", "danger")
        return redirect(url_for('main.gestion_annees'))

    csrf_form = CSRFForm()
    if not csrf_form.validate_on_submit():
        flash("Session expirée ou jeton CSRF invalide.", "danger")
        return redirect(url_for('main.activation_annee_confirmation', annee_id=annee_id))

    succes, msg, details = activer_annee_scolaire(ecole_id, annee_id, user_id=current_user.id)
    if not succes:
        flash(msg, "danger")
        return redirect(url_for('main.activation_annee_confirmation', annee_id=annee_id))

    # Activation réussie : mettre à jour session["annee_consultee"] vers la nouvelle année active
    set_annee_consultee(ecole_id, annee_id)
    flash(msg, "success")
    return redirect(url_for('main.gestion_annees'))


@main.route('/annees/<int:annee_id>/structure', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def structure_annee(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez selectionner une ecole.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first_or_404()
    csrf_form = CSRFForm()
    niveaux_actifs = get_niveaux_candidats_annuels(ecole_id)

    if request.method == 'POST':
        if annee.statut == "archivee":
            flash("Impossible de modifier la structure d'une annee archivee.", "warning")
            return redirect(url_for('main.structure_annee', annee_id=annee.id))
        result, error = sauvegarder_selection_annuelle(
            ecole_id,
            annee.id,
            request.form.getlist('niveau_ids'),
        )
        if error:
            db.session.rollback()
            flash(error, "warning")
            return redirect(url_for('main.structure_annee', annee_id=annee.id))
        db.session.commit()
        if result["closed_classes"]:
            flash(f"Structure enregistree. {result['closed_classes']} classe(s) fermee(s).", "success")
        else:
            flash("Structure annuelle enregistree.", "success")
        return redirect(url_for('main.structure_annee', annee_id=annee.id))

    classes = classes_triees_pedagogique(
        Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
    ).all()
    cours_counts = dict(
        db.session.query(Cours.classe_id, db.func.count(Cours.id))
        .filter(Cours.ecole_id == ecole_id, Cours.classe_id.in_([c.id for c in classes] or [-1]))
        .group_by(Cours.classe_id)
        .all()
    )
    cours_par_classe = {}
    if classes:
        for cours in (
            Cours.query
            .filter(Cours.ecole_id == ecole_id, Cours.classe_id.in_([c.id for c in classes]))
            .order_by(Cours.nom.asc())
            .all()
        ):
            cours_par_classe.setdefault(cours.classe_id, []).append(cours)
    total_cours = sum(cours_counts.values())

    source_annee = (
        AnneeScolaire.query
        .filter(
            AnneeScolaire.ecole_id == ecole_id,
            AnneeScolaire.id != annee.id,
            AnneeScolaire.date_debut < annee.date_debut,
        )
        .order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc())
        .first()
    )
    cours_par_niveau = {niveau.id: [] for niveau in niveaux_actifs}
    cours_source_classes = classes
    if not cours_source_classes and source_annee:
        cours_source_classes = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=source_annee.id).all()
    if cours_source_classes:
        source_class_ids = [classe.id for classe in cours_source_classes]
        niveau_by_classe = {classe.id: classe.niveau_id for classe in cours_source_classes if classe.niveau_id}
        seen_by_niveau = {niveau.id: set() for niveau in niveaux_actifs}
        for cours in (
            Cours.query
            .filter(Cours.ecole_id == ecole_id, Cours.classe_id.in_(source_class_ids))
            .order_by(Cours.nom.asc())
            .all()
        ):
            niveau_id = niveau_by_classe.get(cours.classe_id)
            if niveau_id not in seen_by_niveau:
                continue
            key = (cours.nom or "").strip().lower()
            if key and key not in seen_by_niveau[niveau_id]:
                seen_by_niveau[niveau_id].add(key)
                cours_par_niveau[niveau_id].append(cours.nom)

    selection = get_selection_annuelle(ecole_id, annee.id)
    selected_niveau_ids = selection if selection is not None else {niveau.id for niveau in niveaux_actifs}
    grouped = {"primaire": {}, "college": {}, "lycee": {}, "autre": {}}
    cycle_labels = {"primaire": "Primaire", "college": "College", "lycee": "Lycee", "autre": "Autre"}
    for niveau in niveaux_actifs:
        cycle = niveau.cycle if niveau.cycle in grouped else "autre"
        grouped[cycle].setdefault(niveau.nom, {"niveau": niveau})

    return render_template(
        'structure_annee.html',
        annee=annee,
        source_annee=source_annee,
        annee_consultee=get_annee_consultee(ecole_id),
        classes=classes,
        grouped=grouped,
        cycle_labels=cycle_labels,
        cours_counts=cours_counts,
        total_cours=total_cours,
        cours_par_classe=cours_par_classe,
        cours_par_niveau=cours_par_niveau,
        niveaux_actifs=niveaux_actifs,
        selected_niveau_ids=selected_niveau_ids,
        has_saved_selection=selection is not None,
        csrf_form=csrf_form,
    )


@main.route('/annees/<int:source_id>/passage/<int:cible_id>', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
def passage_annee(source_id, cible_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee_source, annee_cible, error = valider_contexte_passage(ecole_id, source_id, cible_id)
    if error:
        flash(error, "danger")
        return redirect(url_for('main.gestion_annees'))

    # Structure cible utilisable 
    niveaux_cible_actifs = get_niveaux_annuels_actifs(ecole_id, cible_id)
    classes_ouvertes_cible_count = Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=cible_id,
        statut='ouverte'
    ).count()
    structure_prete = bool(niveaux_cible_actifs and classes_ouvertes_cible_count > 0)

    # Inscriptions source pour cette école
    inscriptions_source = (
        Inscription.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=source_id)
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .options(
            db.joinedload(Inscription.eleve),
            db.joinedload(Inscription.classe).joinedload(Classe.niveau_scolaire)
        )
        .order_by(Inscription.classe_id.asc(), Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )

    # Inscriptions cible existantes
    inscriptions_cible = {
        insc.eleve_id: insc
        for insc in Inscription.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=cible_id)
        .options(db.joinedload(Inscription.classe))
        .all()
    }

    # Préparation des lignes avec moyennes annuelles
    moyennes_eleves = get_moyennes_annuelles_eleves(ecole_id, source_id)
    items = []
    classes_source_set = set()
    for insc_src in inscriptions_source:
        eleve = insc_src.eleve
        classe_src = insc_src.classe
        if classe_src:
            classes_source_set.add(classe_src)
        insc_cible = inscriptions_cible.get(eleve.id)

        est_traite = False
        detail_statut = "Non traité"

        if insc_cible:
            est_traite = True
            nom_classe_cible = insc_cible.classe.nom if insc_cible.classe else "Classe inconnue"
            suffixe = " (Préinscrit)" if insc_cible.statut == "preinscrit" else ""
            if insc_src.decision_fin_annee == "passage":
                detail_statut = f"Passage → {nom_classe_cible}{suffixe}"
            elif insc_src.decision_fin_annee == "redoublement":
                detail_statut = f"Redoublement → {nom_classe_cible}{suffixe}"
            else:
                detail_statut = f"{'Préinscrit' if insc_cible.statut == 'preinscrit' else 'Inscrit'} → {nom_classe_cible}"
        elif insc_src.decision_fin_annee in ("transfert", "sortie", "diplome"):
            est_traite = True
            labels = {
                "transfert": "Transfert",
                "sortie": "Sortie",
                "diplome": "Diplôme",
            }
            detail_statut = labels.get(insc_src.decision_fin_annee, insc_src.decision_fin_annee.capitalize())
        elif insc_src.statut in ("transfere", "sorti", "diplome"):
            est_traite = True
            detail_statut = insc_src.statut.capitalize()

        moyenne = moyennes_eleves.get(eleve.id)
        suggestion = "passage" if (moyenne is not None and moyenne >= 10.0) else ("redoublement" if moyenne is not None else None)

        items.append({
            "eleve": eleve,
            "inscription_source": insc_src,
            "classe_source": classe_src,
            "inscription_cible": insc_cible,
            "est_traite": est_traite,
            "detail_statut": detail_statut,
            "moyenne": moyenne,
            "suggestion": suggestion,
        })

    # Compteurs globaux
    total_eleves = len(items)
    nb_traites = sum(1 for item in items if item["est_traite"])
    nb_a_traiter = total_eleves - nb_traites

    # Filtres éventuels
    classe_id_filtre = request.args.get('classe_id', type=int)
    statut_filtre = request.args.get('statut', 'tous')

    items_affiches = items
    if classe_id_filtre:
        items_affiches = [it for it in items_affiches if it["classe_source"] and it["classe_source"].id == classe_id_filtre]
    if statut_filtre == 'a_traiter':
        items_affiches = [it for it in items_affiches if not it["est_traite"]]
    elif statut_filtre == 'traites':
        items_affiches = [it for it in items_affiches if it["est_traite"]]

    classes_source_disponibles = sorted(
        list(classes_source_set),
        key=lambda c: (c.niveau_scolaire.ordre if getattr(c, 'niveau_scolaire', None) else 999, c.nom or "")
    )

    classes_cible_ouvertes = [
        c for c in classes_triees_pedagogique(Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_cible.id)).all()
        if classe_est_ouverte(c)
    ]

    # Regroupement des items par classe source (affichage par classe signature KLASORA)
    classes_dict = {}
    sans_classe_items = []

    for it in items_affiches:
        cl = it["classe_source"]
        if cl:
            if cl.id not in classes_dict:
                classes_dict[cl.id] = {
                    "id": cl.id,
                    "classe": cl,
                    "nom": cl.nom,
                    "niveau": cl.niveau_scolaire.nom if getattr(cl, 'niveau_scolaire', None) else (cl.niveau or ""),
                    "salle": getattr(cl, 'salle', None),
                    "eleves": [],
                    "total_count": 0,
                    "traites_count": 0,
                    "a_traiter_count": 0,
                }
            classes_dict[cl.id]["eleves"].append(it)
            classes_dict[cl.id]["total_count"] += 1
            if it["est_traite"]:
                classes_dict[cl.id]["traites_count"] += 1
            else:
                classes_dict[cl.id]["a_traiter_count"] += 1
        else:
            sans_classe_items.append(it)

    classes_groupes = []
    for cl in classes_source_disponibles:
        if cl.id in classes_dict:
            classes_groupes.append(classes_dict[cl.id])
    for cl_id, grp in classes_dict.items():
        if grp not in classes_groupes:
            classes_groupes.append(grp)

    if sans_classe_items:
        classes_groupes.append({
            "id": "sans-classe",
            "classe": None,
            "nom": "Élèves sans classe",
            "niveau": "",
            "salle": "",
            "eleves": sans_classe_items,
            "total_count": len(sans_classe_items),
            "traites_count": sum(1 for it in sans_classe_items if it["est_traite"]),
            "a_traiter_count": sum(1 for it in sans_classe_items if not it["est_traite"]),
        })

    csrf_form = CSRFForm()
    return render_template(
        'passage_annee.html',
        annee_source=annee_source,
        annee_cible=annee_cible,
        items=items_affiches,
        classes_groupes=classes_groupes,
        total_eleves=total_eleves,
        nb_traites=nb_traites,
        nb_a_traiter=nb_a_traiter,
        structure_prete=structure_prete,
        classes_source_disponibles=classes_source_disponibles,
        classes_cible_ouvertes=classes_cible_ouvertes,
        classe_id_filtre=classe_id_filtre,
        statut_filtre=statut_filtre,
        csrf_form=csrf_form,
    )


@main.route('/annees/<int:source_id>/passage/<int:cible_id>/eleves/<int:eleve_id>', methods=['GET', 'POST'], endpoint='passage_eleve')
@main.route('/annees/<int:source_id>/passage/<int:cible_id>/eleves/<int:eleve_id>/executer', methods=['POST'], endpoint='passage_eleve_executer')
@login_required
@role_required('admin', 'super_admin')
def passage_eleve(source_id, cible_id, eleve_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee_source, annee_cible, error = valider_contexte_passage(ecole_id, source_id, cible_id)
    if error:
        flash(error, "danger")
        return redirect(url_for('main.gestion_annees'))

    # Protection absolue : année source archivée = lecture seule / aucune modification
    if annee_source.statut == "archivee":
        flash(
            "Cette année scolaire est archivée. "
            "Les décisions de fin d'année sont désormais en lecture seule.",
            "warning"
        )
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        flash("Élève introuvable pour cet établissement.", "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    inscription_source = get_inscription(eleve, annee_source)
    if not inscription_source:
        flash("Aucune inscription trouvée pour cet élève dans l'année source.", "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    csrf_form = CSRFForm()

    if request.method == 'POST':
        if not csrf_form.validate_on_submit():
            flash("Session expirée ou jeton CSRF invalide.", "danger")
            return redirect(url_for('main.passage_eleve', source_id=source_id, cible_id=cible_id, eleve_id=eleve.id))

        decision = (request.form.get('decision') or '').strip()
        classe_cible_id = request.form.get('classe_cible_id', type=int)
        motif_sortie = (request.form.get('motif_sortie') or '').strip() or None

        result, error = executer_passage_eleve(
            ecole_id=ecole_id,
            eleve_id=eleve.id,
            annee_source_id=source_id,
            annee_cible_id=cible_id,
            decision=decision,
            classe_cible_id=classe_cible_id,
            motif_sortie=motif_sortie,
        )

        if error:
            db.session.rollback()
            flash(error, "danger")
            return redirect(url_for('main.passage_eleve', source_id=source_id, cible_id=cible_id, eleve_id=eleve.id))

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception(f"Erreur commit passage élève : {e}")
            flash("Erreur lors de l'enregistrement de la décision.", "danger")
            return redirect(url_for('main.passage_eleve', source_id=source_id, cible_id=cible_id, eleve_id=eleve.id))

        if result.get("deja_traite"):
            flash(f"L'élève {eleve.nom} {eleve.prenom} a déjà été traité pour l'année cible.", "info")
        else:
            flash(f"Passage validé avec succès pour {eleve.nom} {eleve.prenom} ({decision}).", "success")

        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    # GET
    classe_source = inscription_source.classe
    niveau_source = classe_source.niveau_scolaire if classe_source else None

    prep_passage = preparer_passage_eleve(ecole_id, eleve.id, source_id, cible_id, 'passage')
    prep_redoublement = preparer_passage_eleve(ecole_id, eleve.id, source_id, cible_id, 'redoublement')

    candidates_passage = prep_passage.get("classes_candidates", [])
    candidates_redoublement = prep_redoublement.get("classes_candidates", [])
    niveau_suivant = prep_passage.get("niveau_cible")

    blocage_niveau = None
    if not niveau_source:
        blocage_niveau = "Le niveau de la classe source n'est pas configuré."

    peut_passer = bool(niveau_source and niveau_source.niveau_suivant is not None)
    peut_diplome = bool(niveau_source and niveau_source.niveau_suivant is None)

    inscription_cible = get_inscription(eleve, annee_cible)

    moyennes_eleves = get_moyennes_annuelles_eleves(ecole_id, source_id)
    moyenne = moyennes_eleves.get(eleve.id)
    suggestion = "passage" if (moyenne is not None and moyenne >= 10.0) else ("redoublement" if moyenne is not None else None)

    return render_template(
        'passage_eleve.html',
        annee_source=annee_source,
        annee_cible=annee_cible,
        eleve=eleve,
        inscription_source=inscription_source,
        classe_source=classe_source,
        niveau_source=niveau_source,
        niveau_suivant=niveau_suivant,
        candidates_passage=candidates_passage,
        candidates_redoublement=candidates_redoublement,
        blocage_niveau=blocage_niveau,
        peut_passer=peut_passer,
        peut_diplome=peut_diplome,
        inscription_cible=inscription_cible,
        moyenne=moyenne,
        suggestion=suggestion,
        csrf_form=csrf_form,
    )


@main.route('/annees/<int:source_id>/passage/<int:cible_id>/masse/apercu', methods=['POST'], endpoint='passage_masse_apercu')
@login_required
@role_required('admin', 'super_admin')
def passage_masse_apercu(source_id, cible_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee_source, annee_cible, error = valider_contexte_passage(ecole_id, source_id, cible_id)
    if error:
        flash(error, "danger")
        return redirect(url_for('main.gestion_annees'))

    # Protection absolue : année source archivée = lecture seule
    if annee_source.statut == "archivee":
        flash(
            "Cette année scolaire est archivée. "
            "Les décisions de fin d'année sont désormais en lecture seule.",
            "warning"
        )
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    csrf_form = CSRFForm()
    if not csrf_form.validate_on_submit():
        flash("Session expirée ou jeton CSRF invalide.", "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    eleve_ids = request.form.getlist('eleve_ids', type=int)
    if not eleve_ids:
        flash("Veuillez sélectionner au moins un élève pour le traitement en masse.", "warning")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    decision = (request.form.get('decision') or '').strip()
    if not decision:
        flash("Veuillez choisir une décision pour le lot d'élèves.", "warning")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    classe_cible_id = request.form.get('classe_cible_id', type=int)
    motif_sortie = (request.form.get('motif_sortie') or '').strip() or None

    prep = preparer_passage_masse(
        ecole_id=ecole_id,
        annee_source_id=source_id,
        annee_cible_id=cible_id,
        eleve_ids=eleve_ids,
        decision=decision,
        classe_cible_id=classe_cible_id,
        motif_sortie=motif_sortie,
    )

    if not prep.get("ok"):
        flash(prep.get("error") or "Erreur lors de la préparation du passage en masse.", "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    return render_template(
        'passage_masse_apercu.html',
        annee_source=annee_source,
        annee_cible=annee_cible,
        classe_cible=prep.get("classe_cible"),
        decision=decision,
        motif_sortie=motif_sortie,
        prep=prep,
        items=prep.get("items", []),
        csrf_form=csrf_form,
    )


@main.route('/annees/<int:source_id>/passage/<int:cible_id>/masse/confirmer', methods=['POST'], endpoint='passage_masse_confirmer')
@login_required
@role_required('admin', 'super_admin')
def passage_masse_confirmer(source_id, cible_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee_source, annee_cible, error = valider_contexte_passage(ecole_id, source_id, cible_id)
    if error:
        flash(error, "danger")
        return redirect(url_for('main.gestion_annees'))

    # Protection absolue : année source archivée = lecture seule
    if annee_source.statut == "archivee":
        flash(
            "Cette année scolaire est archivée. "
            "Les décisions de fin d'année sont désormais en lecture seule.",
            "warning"
        )
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    csrf_form = CSRFForm()
    if not csrf_form.validate_on_submit():
        flash("Session expirée ou jeton CSRF invalide.", "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    eleve_ids = request.form.getlist('eleve_ids', type=int)
    if not eleve_ids:
        flash("Aucun élève sélectionné pour la confirmation.", "warning")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    decision = (request.form.get('decision') or '').strip()
    if not decision:
        flash("Décision manquante pour la confirmation.", "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    classe_cible_id = request.form.get('classe_cible_id', type=int)
    motif_sortie = (request.form.get('motif_sortie') or '').strip() or None

    rapport, err = executer_passage_masse(
        ecole_id=ecole_id,
        annee_source_id=source_id,
        annee_cible_id=cible_id,
        eleve_ids=eleve_ids,
        decision=decision,
        classe_cible_id=classe_cible_id,
        motif_sortie=motif_sortie,
    )

    if err:
        flash(err, "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    return render_template(
        'passage_masse_rapport.html',
        annee_source=annee_source,
        annee_cible=annee_cible,
        decision=decision,
        rapport=rapport,
        csrf_form=csrf_form,
    )


@main.route('/annees/<int:annee_id>/reinscrire_eleve', methods=['POST'], endpoint='reinscrire_ancien_eleve')
@login_required
@role_required('admin', 'super_admin')
def reinscrire_ancien_eleve_route(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    eleve_id = request.form.get('eleve_id', type=int)
    classe_cible_id = request.form.get('classe_cible_id', type=int)
    statut = (request.form.get('statut') or 'inscrit').strip()

    if not eleve_id or not classe_cible_id:
        flash("Informations incomplètes (élève ou classe cible manquante).", "danger")
        return redirect(request.referrer or url_for('main.gestion_annees'))

    inscription, error = reinscrire_ancien_eleve(
        eleve_id=eleve_id,
        annee_active_id=annee_id,
        classe_cible_id=classe_cible_id,
        ecole_id=ecole_id,
        statut=statut,
    )
    if error:
        flash(error, "danger")
        return redirect(request.referrer or url_for('main.gestion_annees'))

    try:
        db.session.commit()
        flash("Élève réinscrit avec succès pour cette année scolaire.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la réinscription : {e}", "danger")

    return redirect(request.referrer or url_for('main.gestion_annees'))


@main.route('/annees/<int:source_id>/passage/<int:cible_id>/annuler/<int:eleve_id>', methods=['POST'], endpoint='annuler_decision_eleve')
@login_required
@role_required('admin', 'super_admin')
def annuler_decision_eleve(source_id, cible_id, eleve_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    csrf_form = CSRFForm()
    if not csrf_form.validate_on_submit():
        flash("Session expirée ou jeton CSRF invalide.", "danger")
        return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))

    ok, message = annuler_decision_passage(
        eleve_id=eleve_id,
        annee_source_id=source_id,
        annee_cible_id=cible_id,
        ecole_id=ecole_id,
    )
    if ok:
        flash(message, "success")
    else:
        flash(message, "danger")

    return redirect(url_for('main.passage_annee', source_id=source_id, cible_id=cible_id))


@main.route('/annees/<int:source_id>/dupliquer_vers/<int:cible_id>', methods=['POST'], endpoint='dupliquer_structure_annee_route')
@login_required
@role_required('admin', 'super_admin')
def dupliquer_structure_annee_route(source_id, cible_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    csrf_form = CSRFForm()
    if not csrf_form.validate_on_submit():
        flash("Session expirée ou jeton CSRF invalide.", "danger")
        return redirect(request.form.get('next') or request.referrer or url_for('main.onboarding_rentree', cible_id=cible_id))

    ok, message = dupliquer_structure_annee(
        annee_source_id=source_id,
        annee_cible_id=cible_id,
        ecole_id=ecole_id,
    )
    if ok:
        flash(message, "success")
    else:
        flash(message, "danger")

    return redirect(request.form.get('next') or request.referrer or url_for('main.onboarding_rentree', cible_id=cible_id))


@main.route('/annees/<int:cible_id>/onboarding_rentree', methods=['GET', 'POST'], endpoint='onboarding_rentree')
@main.route('/annees/<int:cible_id>/wizard', methods=['GET', 'POST'], endpoint='wizard_rentree')
@login_required
@role_required('admin', 'super_admin')
def onboarding_rentree(cible_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez sélectionner un établissement.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee_cible = AnneeScolaire.query.filter_by(id=cible_id, ecole_id=ecole_id).first()
    if not annee_cible:
        flash("Année scolaire introuvable.", "danger")
        return redirect(url_for('main.gestion_annees'))

    if annee_cible.statut == 'archivee':
        flash("Cette année scolaire est archivée et ne peut plus être modifiée.", "warning")
        return redirect(url_for('main.gestion_annees'))

    if annee_cible.statut == 'active':
        flash("Cette année scolaire est déjà active.", "info")
        return redirect(url_for('main.gestion_annees'))

    csrf_form = CSRFForm()
    toutes_annees_ecole = AnneeScolaire.query.filter_by(ecole_id=ecole_id).order_by(AnneeScolaire.date_debut.asc()).all()
    annee_source = determiner_source_passage_pour_cible(annee_cible, toutes_annees_ecole)
    annee_active = next((a for a in toutes_annees_ecole if a.statut == 'active'), None)

    # Traitement des actions POST
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'configurer_semestres':
            fin_s1_str = request.form.get('fin_semestre_1')
            if fin_s1_str:
                try:
                    fin_s1 = datetime.strptime(fin_s1_str, '%Y-%m-%d').date()
                    ok, err = configurer_semestres_annee(ecole_id, annee_cible.id, fin_s1)
                    if ok:
                        flash("Semestres configurés avec succès !", "success")
                        return redirect(url_for('main.onboarding_rentree', cible_id=annee_cible.id, step='2'))
                    else:
                        flash(err or "Erreur lors de la configuration des semestres.", "danger")
                except ValueError:
                    flash("Format de date invalide.", "danger")
            return redirect(url_for('main.onboarding_rentree', cible_id=annee_cible.id, step='1'))

        elif action == 'dupliquer_structure' and annee_source:
            ok, msg = dupliquer_structure_annee(annee_source.id, annee_cible.id, ecole_id)
            if ok:
                flash(msg, "success")
                return redirect(url_for('main.onboarding_rentree', cible_id=annee_cible.id, step='3'))
            else:
                flash(msg, "danger")
                return redirect(url_for('main.onboarding_rentree', cible_id=annee_cible.id, step='2'))

        elif action == 'activer_annee':
            succes, msg, details = activer_annee_scolaire(ecole_id, annee_cible.id, user_id=current_user.id)
            if succes:
                flash(msg, "success")
                return redirect(url_for('main.gestion_annees'))
            else:
                flash(msg, "danger")
                return redirect(url_for('main.onboarding_rentree', cible_id=annee_cible.id, step='4'))

        elif action == 'valider_decisions_classe' and annee_source:
            classe_src_id = request.form.get('classe_source_id', type=int)
            prochaine_classe_id = request.form.get('prochaine_classe_id', type=int)

            nb_succes = 0
            for key, val in request.form.items():
                if key.startswith('decision_'):
                    try:
                        eleve_id = int(key.replace('decision_', ''))
                    except ValueError:
                        continue
                    decision = val.strip()
                    if not decision or decision not in ('passage', 'redoublement', 'sortie', 'transfert', 'diplome'):
                        continue

                    classe_cible_id = request.form.get(f'classe_cible_{eleve_id}', type=int)
                    motif_sortie = request.form.get(f'motif_sortie_{eleve_id}', '').strip() or None

                    res, err = executer_passage_eleve(
                        ecole_id=ecole_id,
                        eleve_id=eleve_id,
                        annee_source_id=annee_source.id,
                        annee_cible_id=annee_cible.id,
                        decision=decision,
                        classe_cible_id=classe_cible_id,
                        motif_sortie=motif_sortie,
                    )
                    if res and res.get('ok'):
                        nb_succes += 1

            try:
                db.session.commit()
                flash(f"{nb_succes} décision(s) enregistrée(s) avec succès pour cette classe !", "success")
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception(f"Erreur validation classe {classe_src_id} : {e}")
                flash("Erreur lors de l'enregistrement des décisions.", "danger")

            return redirect(url_for('main.onboarding_rentree', cible_id=annee_cible.id, step='3', classe_source_id=prochaine_classe_id if prochaine_classe_id else None))

        elif action == 'annuler_decision_eleve' and annee_source:
            eleve_id = request.form.get('eleve_id', type=int)
            classe_src_id = request.form.get('classe_source_id', type=int)
            if eleve_id:
                succes, err = annuler_decision_passage(
                    eleve_id=eleve_id,
                    annee_source_id=annee_source.id,
                    annee_cible_id=annee_cible.id,
                    ecole_id=ecole_id
                )
                if succes:
                    try:
                        db.session.commit()
                        flash("Décision annulée pour cet élève.", "info")
                    except Exception as e:
                        db.session.rollback()
                        flash("Erreur lors de l'annulation.", "danger")
                else:
                    flash(err or "Impossible d'annuler la décision.", "danger")
            return redirect(url_for('main.onboarding_rentree', cible_id=annee_cible.id, step='3', classe_source_id=classe_src_id if classe_src_id else None))

    # 1. Structure & Périodes
    semestres_list = get_semestres_annee(ecole_id, annee_cible.id)
    from types import SimpleNamespace
    ns = SimpleNamespace(
        s1=semestres_list[0] if len(semestres_list) > 0 else None,
        s2=semestres_list[1] if len(semestres_list) > 1 else None,
    )
    classes_cible = classes_triees_pedagogique(
        Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=cible_id)
    ).all()
    total_classes = len(classes_cible)
    classes_ouvertes_count = sum(1 for c in classes_cible if classe_est_ouverte(c))
    periodes_count = PeriodeBulletin.query.filter_by(ecole_id=ecole_id, annee_id=cible_id).count()
    is_cal_cfg = calendrier_configure(ecole_id, cible_id)
    etape1_complete = (classes_ouvertes_count > 0 and (is_cal_cfg or periodes_count >= 2))

    # 2. Décisions du Conseil (Examen Classe par Classe)
    classes_source_statut = []
    classe_active = None
    eleves_classe_active = []
    total_classes_source = 0
    nb_classes_validees = 0
    prochaine_classe_id = None
    classes_cand_passage = []
    classes_cand_redoublement = []

    if annee_source:
        classes_source_query = classes_triees_pedagogique(
            Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_source.id)
        ).all()

        inscriptions_source = (
            Inscription.query
            .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_source.id)
            .join(Eleve, Eleve.id == Inscription.eleve_id)
            .options(
                db.joinedload(Inscription.eleve),
                db.joinedload(Inscription.classe).joinedload(Classe.niveau_scolaire)
            )
            .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
            .all()
        )
        nb_total_source = len(inscriptions_source)

        inscriptions_cible_map = {
            insc.eleve_id: insc
            for insc in Inscription.query
            .filter_by(ecole_id=ecole_id, annee_scolaire_id=cible_id)
            .options(
                db.joinedload(Inscription.classe).joinedload(Classe.niveau_scolaire)
            )
            .all()
        }

        moyennes_eleves = get_moyennes_annuelles_eleves(ecole_id, annee_source.id)

        inscriptions_par_classe = {}
        for insc in inscriptions_source:
            cid = insc.classe_id or 0
            if cid not in inscriptions_par_classe:
                inscriptions_par_classe[cid] = []
            inscriptions_par_classe[cid].append(insc)

        for cl in classes_source_query:
            inscrips_cl = inscriptions_par_classe.get(cl.id, [])
            tot = len(inscrips_cl)
            traites = 0
            for insc_s in inscrips_cl:
                if insc_s.eleve_id in inscriptions_cible_map:
                    traites += 1
                elif insc_s.decision_fin_annee in ("transfert", "sortie", "diplome"):
                    traites += 1
                elif insc_s.statut in ("transfere", "sorti", "diplome"):
                    traites += 1

            terminee = (tot > 0 and traites >= tot)
            classes_source_statut.append({
                "id": cl.id,
                "classe": cl,
                "nom": cl.nom,
                "niveau": cl.niveau_scolaire.nom if cl.niveau_scolaire else (cl.niveau or ""),
                "total_eleves": tot,
                "nb_traites": traites,
                "nb_a_traiter": max(0, tot - traites),
                "est_terminee": terminee,
            })

        total_classes_source = len(classes_source_statut)
        nb_classes_validees = sum(1 for c in classes_source_statut if c["est_terminee"])

        nb_traites = sum(c["nb_traites"] for c in classes_source_statut)
        inscrips_sans_classe = inscriptions_par_classe.get(0, [])
        for insc_s in inscrips_sans_classe:
            if insc_s.eleve_id in inscriptions_cible_map or insc_s.decision_fin_annee in ("transfert", "sortie", "diplome") or insc_s.statut in ("transfere", "sorti", "diplome"):
                nb_traites += 1

        nb_a_traiter = max(0, nb_total_source - nb_traites)
        ratio_decisions = int((nb_traites / nb_total_source * 100)) if nb_total_source > 0 else 100
        etape2_complete = (nb_total_source > 0 and nb_traites >= nb_total_source)

        req_classe_id = request.args.get('classe_source_id', type=int)
        classe_active_statut = None
        if req_classe_id:
            classe_active_statut = next((c for c in classes_source_statut if c["id"] == req_classe_id), None)
        if not classe_active_statut:
            classe_active_statut = next((c for c in classes_source_statut if not c["est_terminee"]), None)
        if not classe_active_statut and classes_source_statut:
            classe_active_statut = classes_source_statut[0]

        if classe_active_statut:
            classe_active = classe_active_statut["classe"]
            classes_suivantes = [c for c in classes_source_statut if c["id"] != classe_active.id and not c["est_terminee"]]
            prochaine_classe_id = classes_suivantes[0]["id"] if classes_suivantes else None

            niv_src = classe_active.niveau_scolaire
            niv_suivant = niv_src.niveau_suivant if niv_src else None

            if niv_suivant:
                classes_cand_passage = [
                    c for c in classes_cible if c.niveau_id == niv_suivant.id and classe_est_ouverte(c)
                ]
            if not classes_cand_passage:
                classes_cand_passage = [c for c in classes_cible if classe_est_ouverte(c)]

            if niv_src:
                classes_cand_redoublement = [
                    c for c in classes_cible if c.niveau_id == niv_src.id and classe_est_ouverte(c)
                ]
            if not classes_cand_redoublement:
                classes_cand_redoublement = [
                    c for c in classes_cible if c.nom == classe_active.nom and classe_est_ouverte(c)
                ]
            if not classes_cand_redoublement:
                classes_cand_redoublement = [c for c in classes_cible if classe_est_ouverte(c)]

            lettre = classe_active.nom.split()[-1] if classe_active.nom else ""
            match_lettre = next((c for c in classes_cand_passage if c.nom.endswith(lettre)), None)
            classe_suggeree_passage_id = match_lettre.id if match_lettre else (classes_cand_passage[0].id if classes_cand_passage else None)

            match_nom = next((c for c in classes_cand_redoublement if c.nom == classe_active.nom), None)
            classe_suggeree_redoublement_id = match_nom.id if match_nom else (classes_cand_redoublement[0].id if classes_cand_redoublement else None)

            inscrips_active = inscriptions_par_classe.get(classe_active.id, [])
            for insc_s in inscrips_active:
                el = insc_s.eleve
                insc_c = inscriptions_cible_map.get(el.id)
                moy = moyennes_eleves.get(el.id)
                sugg = "passage" if (moy is not None and moy >= 10.0) else ("redoublement" if moy is not None else "passage")

                est_t = False
                detail_statut = "En attente"
                if insc_c:
                    est_t = True
                    nom_cl = insc_c.classe.nom if insc_c.classe else "Classe"
                    detail_statut = f"{insc_s.decision_fin_annee.capitalize() if insc_s.decision_fin_annee else 'Orienté'} → {nom_cl}"
                elif insc_s.decision_fin_annee in ("transfert", "sortie", "diplome"):
                    est_t = True
                    detail_statut = insc_s.decision_fin_annee.capitalize()
                elif insc_s.statut in ("transfere", "sorti", "diplome"):
                    est_t = True
                    detail_statut = insc_s.statut.capitalize()

                eleves_classe_active.append({
                    "eleve": el,
                    "inscription_source": insc_s,
                    "inscription_cible": insc_c,
                    "est_traite": est_t,
                    "detail_statut": detail_statut,
                    "moyenne": moy,
                    "suggestion": sugg,
                    "classe_suggeree_passage_id": classe_suggeree_passage_id,
                    "classe_suggeree_redoublement_id": classe_suggeree_redoublement_id,
                })
    else:
        nb_total_source = 0
        nb_traites = 0
        nb_a_traiter = 0
        ratio_decisions = 100
        etape2_complete = True
        total_classes_source = 0
        nb_classes_validees = 0

    # 3. Pointage Financier (Confirmés vs Préinscrits)
    inscriptions_cible = (
        Inscription.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=cible_id)
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .outerjoin(Classe, Classe.id == Inscription.classe_id)
        .options(
            db.joinedload(Inscription.eleve),
            db.joinedload(Inscription.classe)
        )
        .order_by(Classe.nom.asc(), Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )
    nb_confirmes = sum(1 for insc in inscriptions_cible if insc.statut == 'inscrit')
    preinscrits_list = [insc for insc in inscriptions_cible if insc.statut == 'preinscrit']
    nb_preinscrits = len(preinscrits_list)
    total_inscrits_cible = nb_confirmes + nb_preinscrits
    taux_confirmation = int((nb_confirmes / total_inscrits_cible * 100)) if total_inscrits_cible > 0 else 0
    etape3_complete = (total_inscrits_cible > 0 and nb_preinscrits == 0)

    # 4. Lancement Officiel
    etat_prep = get_etat_preparation_annee(ecole_id, cible_id)
    if etat_prep:
        prete_pour_activation = etat_prep["verification"]["prete_pour_activation"]
        bloquants = etat_prep["verification"]["bloquants"]
        avertissements = etat_prep["verification"]["avertissements"]
        progression = etat_prep["progression"]
    else:
        prete_pour_activation = False
        bloquants = []
        avertissements = []
        progression = 0
    etape4_complete = (annee_cible.statut == 'active')

    # Sélection intelligente de l'étape active
    step = request.args.get('step')
    if step not in ['1', '2', '3', '4']:
        if not is_cal_cfg:
            step = '1'
        elif total_classes == 0:
            step = '2'
        elif nb_a_traiter > 0:
            step = '3'
        else:
            step = '4'

    return render_template(
        'onboarding_rentree.html',
        annee=annee_cible,
        annee_cible=annee_cible,
        annee_source=annee_source,
        annee_active=annee_active,
        step=step,
        csrf_form=csrf_form,
        # Étape 1
        ns=ns,
        classes_cible=classes_cible,
        total_classes=total_classes,
        classes_ouvertes_count=classes_ouvertes_count,
        periodes_count=periodes_count,
        is_cal_cfg=is_cal_cfg,
        etape1_complete=etape1_complete,
        # Étape 2 (Décisions par classe)
        nb_total_source=nb_total_source,
        nb_traites=nb_traites,
        nb_a_traiter=nb_a_traiter,
        ratio_decisions=ratio_decisions,
        etape2_complete=etape2_complete,
        classes_source_statut=classes_source_statut,
        classe_active=classe_active,
        eleves_classe_active=eleves_classe_active,
        total_classes_source=total_classes_source,
        nb_classes_validees=nb_classes_validees,
        prochaine_classe_id=prochaine_classe_id,
        classes_cand_passage=classes_cand_passage,
        classes_cand_redoublement=classes_cand_redoublement,
        # Étape 3
        nb_confirmes=nb_confirmes,
        nb_preinscrits=nb_preinscrits,
        total_inscrits_cible=total_inscrits_cible,
        taux_confirmation=taux_confirmation,
        preinscrits_list=preinscrits_list,
        etape3_complete=etape3_complete,
        # Étape 4
        prete_pour_activation=prete_pour_activation,
        bloquants=bloquants,
        avertissements=avertissements,
        progression=progression,
        etape4_complete=etape4_complete,
        etat_prep=etat_prep,
        etat=etat_prep,
    )


