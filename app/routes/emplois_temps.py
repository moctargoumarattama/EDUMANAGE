from . import main
from .common import (
    AjouterEmploiForm,
    Classe,
    Cours,
    DeleteForm,
    EmploiTemps,
    Professeur,
    current_user,
    datetime,
    flash,
    log_action,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    url_for,
    jsonify,
)
from app.utils import get_annee_consultee
from app.utils_classes import classes_triees_pedagogique
from app.services.emploi_temps_annuel import (
    MESSAGE_ANNEE_ARCHIVEE,
    MESSAGE_ANNEE_PLANIFIEE,
    statut_annee_emploi,
    peut_modifier_emploi_temps,
    get_creneaux_annee,
    valider_et_creer_creneau,
    valider_et_modifier_creneau,
    supprimer_creneau,
    get_classes_pour_utilisateur,
    donnees_impression_classe,
)


@main.route('/emplois')
@main.route('/admin/emplois')
@login_required
@role_required('admin', 'professeur', 'parent')
def admin_emplois():
    """
    Liste des emplois du temps organisés par classe - filtrée par école et année consultée.
    Respecte la règle 2C-5D : consomme get_annee_consultee() sans muter la session.
    """
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)

    delete_form = DeleteForm()

    if not annee_consultee:
        flash("Aucune année scolaire active ou configurée.", "warning")
        return render_template(
            'admin_emplois.html',
            emplois=[],
            classes=[],
            emplois_par_classe={},
            emplois_sans_classe=[],
            pagination=None,
            delete_form=delete_form,
            classes_count=0,
            professeurs_count=0,
            salles_count=0,
            annee_consultee=None,
            statut_annee='inconnue',
            peut_modifier=False,
            statut_warning=None,
            now=datetime.now(),
        )

    statut = statut_annee_emploi(annee_consultee)
    peut_modifier = peut_modifier_emploi_temps(annee_consultee)
    statut_warning = None
    if statut == 'archivee':
        statut_warning = MESSAGE_ANNEE_ARCHIVEE
    elif statut == 'planifiee':
        statut_warning = f"{MESSAGE_ANNEE_PLANIFIEE} ({annee_consultee.nom})"

    # Récupération des classes autorisées selon le rôle dans l'année consultée
    classes = get_classes_pour_utilisateur(ecole_id, annee_consultee, current_user)
    classes_ids = [c.id for c in classes]

    # Récupération des créneaux
    if current_user.role == 'professeur':
        prof = Professeur.query.filter_by(utilisateur_id=current_user.id).first()
        prof_id = prof.id if prof else None
        all_emplois = get_creneaux_annee(ecole_id, annee_consultee, professeur_id=prof_id)
    elif current_user.role == 'parent':
        all_creneaux = get_creneaux_annee(ecole_id, annee_consultee)
        all_emplois = [e for e in all_creneaux if e.classe_id in classes_ids]
    else:
        # Admin / Super-admin
        all_emplois = get_creneaux_annee(ecole_id, annee_consultee)

    classes_count = len(classes)
    professeurs_count = Professeur.query.filter_by(ecole_id=ecole_id).count()
    salles_count = len(set([e.salle for e in all_emplois if e.salle]))

    # Organisation des emplois par classe
    emplois_par_classe = {c.id: [] for c in classes}
    emplois_sans_classe = []
    for e in all_emplois:
        if e.classe_id and e.classe_id in emplois_par_classe:
            emplois_par_classe[e.classe_id].append(e)
        elif e.classe and e.classe.id in emplois_par_classe:
            emplois_par_classe[e.classe.id].append(e)
        else:
            emplois_sans_classe.append(e)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.args.get('ajax') == '1':
        return jsonify({
            'emplois': [
                {
                    'id': e.id,
                    'jour': e.jour,
                    'heure_debut': str(e.heure_debut),
                    'heure_fin': str(e.heure_fin),
                    'classe_id': e.classe_id,
                    'classe_nom': e.classe.nom if e.classe else '',
                    'cours_id': e.cours_id,
                    'cours_nom': e.cours.nom if e.cours else '',
                    'professeur_id': e.professeur_id,
                    'professeur_nom': f"{e.professeur.prenom} {e.professeur.nom}" if e.professeur else '',
                    'salle': e.salle or '',
                } for e in all_emplois
            ],
            'classes_count': classes_count,
            'professeurs_count': professeurs_count,
            'salles_count': salles_count,
        })

    return render_template(
        'admin_emplois.html',
        emplois=all_emplois,
        classes=classes,
        emplois_par_classe=emplois_par_classe,
        emplois_sans_classe=emplois_sans_classe,
        pagination=None,
        delete_form=delete_form,
        classes_count=classes_count,
        professeurs_count=professeurs_count,
        salles_count=salles_count,
        annee_consultee=annee_consultee,
        statut_annee=statut,
        peut_modifier=peut_modifier,
        statut_warning=statut_warning,
        now=datetime.now(),
    )


@main.route('/admin/ajouter_emploi', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_emploi():
    """
    Ajout d'un créneau d'emploi du temps - filtré par école et année consultée.
    Autorisé en année active et planifiée. Rejeté en année archivée.
    """
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)

    if not annee_consultee:
        flash("Aucune année scolaire active ou configurée.", "warning")
        return redirect(url_for('main.admin_emplois'))

    if not peut_modifier_emploi_temps(annee_consultee):
        flash(MESSAGE_ANNEE_ARCHIVEE, "warning")
        return redirect(url_for('main.admin_emplois'))

    form = AjouterEmploiForm(annee=annee_consultee)

    # Menus déroulants strictement filtrés sur l'école et l'année consultée
    classes_annee = classes_triees_pedagogique(Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_consultee.id)).all()
    form.classe_id.choices = [(c.id, c.nom) for c in classes_annee]
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.nom).all()]
    
    # Pré-sélection de la classe si spécifiée dans l'URL
    classe_id_arg = request.args.get('classe_id', type=int)
    if request.method == 'GET' and classe_id_arg:
        form.classe_id.data = classe_id_arg

    # Filtrer les cours proposés par la classe sélectionnée
    selected_classe_id = form.classe_id.data or (classes_annee[0].id if classes_annee else None)
    if selected_classe_id:
        cours_query = Cours.query.filter_by(ecole_id=ecole_id, classe_id=selected_classe_id).order_by(Cours.nom).all()
        form.cours_id.choices = [(c.id, c.nom) for c in cours_query]
    else:
        form.cours_id.choices = [(c.id, f"{c.nom} ({c.classe.nom})" if c.classe else c.nom) for c in Cours.query.join(Classe, Cours.classe_id == Classe.id).filter(Classe.annee_scolaire_id == annee_consultee.id, Cours.ecole_id == ecole_id).order_by(Cours.nom).all()]

    if form.validate_on_submit():
        creneau, error = valider_et_creer_creneau(
            ecole_id=ecole_id,
            annee=annee_consultee,
            classe_id=form.classe_id.data,
            cours_id=form.cours_id.data,
            professeur_id=form.professeur_id.data,
            jour=form.jour.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
            salle=form.salle.data,
        )

        if error:
            flash(error, "danger")
            return render_template('admin_ajouter_emploi.html', form=form, annee_consultee=annee_consultee)

        log_action(current_user, f"Ajout créneau emploi du temps ID={creneau.id} (Classe ID={creneau.classe_id})")
        flash("Créneau d'emploi du temps ajouté avec succès !", "success")
        return redirect(url_for('main.admin_emplois'))

    return render_template('admin_ajouter_emploi.html', form=form, annee_consultee=annee_consultee)


@main.route('/emploi/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_emploi(id):
    """
    Modification d'un créneau d'emploi du temps.
    Autorisé en année active et planifiée. Rejeté en année archivée.
    """
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    emploi = EmploiTemps.query.filter_by(id=id, ecole_id=ecole_id).first_or_404()

    if not annee_consultee or emploi.classe.annee_scolaire_id != annee_consultee.id:
        flash("Ce créneau n'appartient pas à l'année scolaire consultée.", "warning")
        return redirect(url_for('main.admin_emplois'))

    if not peut_modifier_emploi_temps(annee_consultee):
        flash(MESSAGE_ANNEE_ARCHIVEE, "warning")
        return redirect(url_for('main.admin_emplois'))

    form = AjouterEmploiForm(obj=emploi, annee=annee_consultee)
    classes_annee = classes_triees_pedagogique(Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_consultee.id)).all()
    form.classe_id.choices = [(c.id, c.nom) for c in classes_annee]
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.nom).all()]
    
    # Cours de la classe du créneau
    cours_classe = Cours.query.filter_by(ecole_id=ecole_id, classe_id=form.classe_id.data or emploi.classe_id).order_by(Cours.nom).all()
    form.cours_id.choices = [(c.id, c.nom) for c in cours_classe]

    if form.validate_on_submit():
        creneau, error = valider_et_modifier_creneau(
            ecole_id=ecole_id,
            annee=annee_consultee,
            creneau_id=emploi.id,
            classe_id=form.classe_id.data,
            cours_id=form.cours_id.data,
            professeur_id=form.professeur_id.data,
            jour=form.jour.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
            salle=form.salle.data,
        )

        if error:
            flash(error, "danger")
            return render_template('modifier_emploi.html', form=form, emploi=emploi, annee_consultee=annee_consultee)

        log_action(current_user, f"Modification créneau emploi du temps ID={emploi.id}")
        flash("Créneau d'emploi du temps modifié avec succès.", "success")
        return redirect(url_for('main.admin_emplois'))

    return render_template('modifier_emploi.html', form=form, emploi=emploi, annee_consultee=annee_consultee)


@main.route('/emploi/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_emploi(id):
    """
    Suppression d'un créneau d'emploi du temps.
    Autorisé en année active et planifiée. Rejeté en année archivée.
    """
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)

    if not annee_consultee:
        flash("Aucune année scolaire active ou configurée.", "warning")
        return redirect(url_for('main.admin_emplois'))

    success, error = supprimer_creneau(ecole_id, annee_consultee, id)
    if not success:
        flash(error or "Erreur lors de la suppression du créneau.", "danger")
    else:
        log_action(current_user, f"Suppression créneau emploi du temps ID={id}")
        flash("Créneau d'emploi du temps supprimé avec succès.", "success")

    return redirect(url_for('main.admin_emplois'))


@main.route('/emploi/classe/<int:classe_id>/export_json')
@login_required
@role_required('admin', 'professeur', 'parent')
def export_json_classe(classe_id):
    """
    Export JSON de l'emploi du temps d'une classe pour l'année consultée.
    """
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    donnees, error = donnees_impression_classe(ecole_id, annee_consultee, classe_id, user=current_user)
    if error:
        return jsonify({"success": False, "message": error}), 403
    return jsonify({"success": True, "data": donnees})


@main.route('/emploi/classe/<int:classe_id>/imprimer')
@login_required
@role_required('admin', 'professeur', 'parent')
def imprimer_classe(classe_id):
    """
    Vue imprimable de l'emploi du temps d'une classe pour l'année consultée.
    """
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    donnees, error = donnees_impression_classe(ecole_id, annee_consultee, classe_id, user=current_user)
    if error:
        flash(error, "danger")
        return redirect(url_for('main.admin_emplois'))

    return render_template(
        'imprimer_emploi_classe.html',
        donnees=donnees,
        annee_consultee=annee_consultee,
        now=datetime.now(),
    )


@main.route('/api/cours_classe/<int:classe_id>')
@login_required
def api_cours_classe(classe_id):
    """
    API AJAX pour charger dynamiquement les cours d'une classe dans l'année consultée.
    """
    ecole_id = current_user.ecole_id
    annee_consultee = get_annee_consultee(ecole_id)
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe or not annee_consultee or classe.annee_scolaire_id != annee_consultee.id:
        return jsonify([]), 403

    cours = Cours.query.filter_by(classe_id=classe.id, ecole_id=ecole_id).order_by(Cours.nom).all()
    return jsonify([
        {
            "id": c.id,
            "nom": c.nom,
            "professeur_id": c.professeur_id,
            "professeur_nom": f"{c.professeur.prenom} {c.professeur.nom}" if c.professeur else None,
        }
        for c in cours
    ])
