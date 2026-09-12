from . import main
from .common import (
    AnneeScolaire,
    CSRFForm,
    Ecole,
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
from app.services.classes_annuelles import preparer_structure_annee
from app.services.niveaux import get_niveaux_actifs
from app.models import Classe, Cours


def _current_ecole_id_for_annees():
    return current_user.ecole_id if current_user.role != 'super_admin' else session.get('ecole_id')


@main.route('/annees', methods=['GET', 'POST'])
@login_required
@role_required('admin')
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
            annee = AnneeScolaire.query.get(int(annee_id))
            if annee and annee.ecole_id in [e.id for e in ecoles]:
                # Désactiver uniquement les années de la même école
                AnneeScolaire.query.filter_by(ecole_id=annee.ecole_id).update({'statut': 'archivee'})
                annee.statut = 'active'
                db.session.commit()
                flash(f"L'année {annee.nom} est maintenant active.", "success")
            else:
                flash("Action non autorisée pour cette école.", "danger")

        elif action == 'ajouter':
            nom = request.form.get('nom')
            date_debut_str = request.form.get('date_debut')
            date_fin_str = request.form.get('date_fin')
            ecole_id = request.form.get('ecole_id')

            try:
                # ✅ Sécurisation : si l'admin n’a qu’une seule école, on force automatiquement
                if not ecole_id and len(ecoles) == 1:
                    ecole_id = ecoles[0].id

                if not (nom and date_debut_str and date_fin_str and ecole_id):
                    flash("Tous les champs sont obligatoires.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                ecole_id = int(ecole_id)
                if ecole_id not in [e.id for e in ecoles]:
                    flash("Vous ne pouvez pas créer une année pour cette école.", "danger")
                    return redirect(url_for('main.gestion_annees'))

                date_debut = datetime.strptime(date_debut_str, "%Y-%m-%d").date()
                date_fin = datetime.strptime(date_fin_str, "%Y-%m-%d").date()

                nouvelle_annee = AnneeScolaire(
                    nom=nom,
                    date_debut=date_debut,
                    date_fin=date_fin,
                    statut='planifiee',
                    ecole_id=ecole_id  # ✅ Jamais None
                )
                db.session.add(nouvelle_annee)
                db.session.commit()
                flash(f"Nouvelle année {nom} ajoutée.", "success")

            except Exception as e:
                db.session.rollback()
                current_app.logger.exception(f"Erreur lors de l'ajout de l'année : {e}")
                flash("Une erreur est survenue lors de l'ajout de l'année.", "danger")

        from app.utils import get_school_setup_state
        if current_user.role == 'admin' and current_user.ecole_id and not get_school_setup_state(current_user.ecole_id)['setup_complete']:
            return redirect(url_for('main.onboarding'))

        return redirect(url_for('main.gestion_annees'))

    return render_template('gestion_annees.html', annees=annees, ecoles=ecoles, csrf_form=csrf_form)

@main.route('/changer_annee/<int:annee_id>', methods=['POST'])
@login_required
@role_required('admin')
def changer_annee(annee_id):
    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=current_user.ecole_id).first_or_404()
    try:
        # Désactiver toutes les années de la même école
        AnneeScolaire.query.filter_by(ecole_id=annee.ecole_id).update({'statut': 'archivee'})
        # Activer l'année sélectionnée
        annee.statut = 'active'
        db.session.commit()
        flash(f"L'année {annee.nom} est maintenant active.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de l'activation: {str(e)}", "danger")

    from app.utils import get_school_setup_state
    if current_user.role == 'admin' and current_user.ecole_id and not get_school_setup_state(current_user.ecole_id)['setup_complete']:
        return redirect(url_for('main.onboarding'))

    return redirect(request.referrer or url_for('main.gestion_annees'))


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


@main.route('/annees/<int:annee_id>/structure')
@login_required
@role_required('admin', 'super_admin')
def structure_annee(annee_id):
    ecole_id = _current_ecole_id_for_annees()
    if not ecole_id:
        flash("Veuillez selectionner une ecole.", "warning")
        return redirect(url_for('main.gestion_annees'))

    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first_or_404()
    csrf_form = CSRFForm()
    classes = (
        Classe.query
        .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id)
        .order_by(Classe.niveau_id.asc(), Classe.nom.asc(), Classe.id.asc())
        .all()
    )
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

    niveaux_actifs = get_niveaux_actifs(ecole_id)
    grouped = {"primaire": {}, "college": {}, "lycee": {}, "autre": {}}
    cycle_labels = {"primaire": "Primaire", "college": "College", "lycee": "Lycee", "autre": "Autre"}
    for niveau in niveaux_actifs:
        cycle = niveau.cycle if niveau.cycle in grouped else "autre"
        grouped[cycle].setdefault(niveau.nom, {"niveau": niveau, "classes": []})
    for classe in classes:
        niveau = classe.niveau_scolaire
        cycle = niveau.cycle if niveau and niveau.cycle in grouped else "autre"
        niveau_label = niveau.nom if niveau else (classe.niveau or "Sans niveau")
        grouped[cycle].setdefault(niveau_label, {"niveau": niveau, "classes": []})["classes"].append(classe)

    return render_template(
        'structure_annee.html',
        annee=annee,
        classes=classes,
        grouped=grouped,
        cycle_labels=cycle_labels,
        cours_counts=cours_counts,
        total_cours=total_cours,
        cours_par_classe=cours_par_classe,
        niveaux_actifs=niveaux_actifs,
        csrf_form=csrf_form,
    )
