from . import main
from .common import (
    AjouterEmploiForm,
    Classe,
    Cours,
    DeleteForm,
    EmploiTemps,
    Professeur,
    current_app,
    current_user,
    datetime,
    db,
    flash,
    get_ecole_filter_query,
    log_action,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    url_for,
)


@main.route('/admin/emplois')
@login_required
@role_required('admin')
def admin_emplois():
    """Liste des emplois du temps - filtrée par école"""
    page = request.args.get('page', 1, type=int)
    per_page = 30

    query = get_ecole_filter_query(EmploiTemps).order_by(EmploiTemps.jour, EmploiTemps.heure_debut)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    delete_form = DeleteForm()

    # Compteurs statistiques (pour l'aperçu global)
    ecole_id = current_user.ecole_id
    classes_count = Classe.query.filter_by(ecole_id=ecole_id).count()
    professeurs_count = Professeur.query.filter_by(ecole_id=ecole_id).count()
    salles_count = EmploiTemps.query.filter_by(ecole_id=ecole_id).distinct(EmploiTemps.salle).count()

    return render_template('admin_emplois.html',
                           emplois=pagination.items,
                           pagination=pagination,
                           delete_form=delete_form,
                           classes_count=classes_count,
                           professeurs_count=professeurs_count,
                           salles_count=salles_count,
                           now=datetime.now())

@main.route('/admin/ajouter_emploi', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_emploi():
    """Ajout d'un emploi du temps - filtré par école"""
    ecole_id = current_user.ecole_id
    form = AjouterEmploiForm()

    # Menus déroulants filtrés par école
    form.classe_id.choices = [(c.id, c.nom) for c in Classe.query.filter_by(ecole_id=ecole_id).order_by(Classe.nom)]
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.nom)]
    form.cours_id.choices = [(c.id, c.nom) for c in Cours.query.filter_by(ecole_id=ecole_id).order_by(Cours.nom)]

    if form.validate_on_submit():
        # Vérifier les doublons
        conflit = EmploiTemps.query.filter_by(
            classe_id=form.classe_id.data,
            jour=form.jour.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
            ecole_id=ecole_id
        ).first()

        if conflit:
            flash("⚠️ Cet emploi existe déjà pour cette classe à la même heure.", "warning")
            return redirect(url_for('main.admin_emplois'))

        # Création
        emploi = EmploiTemps(
            classe_id=form.classe_id.data,
            professeur_id=form.professeur_id.data,
            cours_id=form.cours_id.data,
            jour=form.jour.data,
            salle=form.salle.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
            ecole_id=ecole_id
        )
        db.session.add(emploi)
        db.session.commit()

        # Journalisation
        log_action(current_user, f"Ajout emploi du temps pour la classe ID={form.classe_id.data}")

        flash("✅ Emploi du temps ajouté avec succès !", "success")
        return redirect(url_for('main.admin_emplois'))

    return render_template('admin_ajouter_emploi.html', form=form)

@main.route('/emploi/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_emploi(id):
    emploi = EmploiTemps.query.get_or_404(id)
    form = AjouterEmploiForm(obj=emploi)  # Pré-remplit le formulaire avec l'objet existant

    if form.validate_on_submit():
        emploi.classe_id = form.classe_id.data
        emploi.professeur_id = form.professeur_id.data
        emploi.cours_id = form.cours_id.data
        emploi.jour = form.jour.data
        emploi.heure_debut = form.heure_debut.data
        emploi.heure_fin = form.heure_fin.data
        emploi.salle = form.salle.data
        db.session.commit()
        flash('Emploi du temps modifié avec succès', 'success')
        return redirect(url_for('main.admin_emplois'))

    return render_template('modifier_emploi.html', form=form, emploi=emploi)

@main.route('/emploi/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_emploi(id):
    emploi = EmploiTemps.query.get_or_404(id)
    try:
        db.session.delete(emploi)
        db.session.commit()
        flash('Emploi du temps supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression emploi: {e}")
        flash('Erreur lors de la suppression de l\'emploi du temps.', 'danger')
    return redirect(url_for('main.admin_emplois'))
