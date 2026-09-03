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
    """Liste des emplois du temps - filtrÃ©e par Ã©cole"""
    page = request.args.get('page', 1, type=int)
    per_page = 30

    query = get_ecole_filter_query(EmploiTemps).order_by(EmploiTemps.jour, EmploiTemps.heure_debut)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    delete_form = DeleteForm()

    # Compteurs statistiques (pour l'aperÃ§u global)
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
    """Ajout d'un emploi du temps - filtrÃ© par Ã©cole"""
    ecole_id = current_user.ecole_id
    form = AjouterEmploiForm()

    # Menus dÃ©roulants filtrÃ©s par Ã©cole
    form.classe_id.choices = [(c.id, c.nom) for c in Classe.query.filter_by(ecole_id=ecole_id).order_by(Classe.nom)]
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.nom)]
    form.cours_id.choices = [(c.id, c.nom) for c in Cours.query.filter_by(ecole_id=ecole_id).order_by(Cours.nom)]

    if form.validate_on_submit():
        # VÃ©rifier les doublons
        classe = Classe.query.filter_by(id=form.classe_id.data, ecole_id=ecole_id).first()
        professeur = Professeur.query.filter_by(id=form.professeur_id.data, ecole_id=ecole_id).first()
        cours = Cours.query.filter_by(id=form.cours_id.data, ecole_id=ecole_id).first()
        if not classe or not professeur or not cours:
            flash("Classe, professeur ou cours invalide pour votre ecole.", "danger")
            return redirect(url_for('main.admin_emplois'))

        conflit = EmploiTemps.query.filter_by(
            classe_id=form.classe_id.data,
            jour=form.jour.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
            ecole_id=ecole_id
        ).first()

        if conflit:
            flash("âš ï¸ Cet emploi existe dÃ©jÃ  pour cette classe Ã  la mÃªme heure.", "warning")
            return redirect(url_for('main.admin_emplois'))

        # CrÃ©ation
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

        flash("âœ… Emploi du temps ajoutÃ© avec succÃ¨s !", "success")
        return redirect(url_for('main.admin_emplois'))

    return render_template('admin_ajouter_emploi.html', form=form)

@main.route('/emploi/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_emploi(id):
    emploi = EmploiTemps.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()
    form = AjouterEmploiForm(obj=emploi)  # PrÃ©-remplit le formulaire avec l'objet existant
    form.classe_id.choices = [(c.id, c.nom) for c in Classe.query.filter_by(ecole_id=current_user.ecole_id).order_by(Classe.nom)]
    form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in Professeur.query.filter_by(ecole_id=current_user.ecole_id).order_by(Professeur.nom)]
    form.cours_id.choices = [(c.id, c.nom) for c in Cours.query.filter_by(ecole_id=current_user.ecole_id).order_by(Cours.nom)]

    if form.validate_on_submit():
        classe = Classe.query.filter_by(id=form.classe_id.data, ecole_id=current_user.ecole_id).first()
        professeur = Professeur.query.filter_by(id=form.professeur_id.data, ecole_id=current_user.ecole_id).first()
        cours = Cours.query.filter_by(id=form.cours_id.data, ecole_id=current_user.ecole_id).first()
        if not classe or not professeur or not cours:
            flash("Classe, professeur ou cours invalide pour votre ecole.", "danger")
            return redirect(url_for('main.modifier_emploi', id=emploi.id))

        emploi.classe_id = form.classe_id.data
        emploi.professeur_id = form.professeur_id.data
        emploi.cours_id = form.cours_id.data
        emploi.jour = form.jour.data
        emploi.heure_debut = form.heure_debut.data
        emploi.heure_fin = form.heure_fin.data
        emploi.salle = form.salle.data
        db.session.commit()
        flash('Emploi du temps modifiÃ© avec succÃ¨s', 'success')
        return redirect(url_for('main.admin_emplois'))

    return render_template('modifier_emploi.html', form=form, emploi=emploi)

@main.route('/emploi/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_emploi(id):
    emploi = EmploiTemps.query.filter_by(id=id, ecole_id=current_user.ecole_id).first_or_404()
    try:
        db.session.delete(emploi)
        db.session.commit()
        flash('Emploi du temps supprimÃ© avec succÃ¨s', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression emploi: {e}")
        flash('Erreur lors de la suppression de l\'emploi du temps.', 'danger')
    return redirect(url_for('main.admin_emplois'))

