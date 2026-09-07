from . import main
from .common import (
    Ecole,
    Eleve,
    GererEcolesForm,
    Utilisateur,
    current_app,
    current_user,
    db,
    envoyer_email,
    flash,
    generate_password_hash,
    gestion_ecole,
    get_ecole_filter_query,
    jsonify,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    secrets,
    session,
    url_for,
)


@main.route('/choisir-ecole')
@login_required
def choisir_ecole():
    """Afficher automatiquement les Ã©coles accessibles et leurs journaux/problÃ¨mes"""
    
    # Pour un admin normal : rÃ©cupÃ©rer son Ã©cole et ses Ã©coles gÃ©rÃ©es
    if current_user.role != "super_admin":
        ecoles = []
        if current_user.ecole:
            ecoles.append(current_user.ecole)
        if getattr(current_user, 'ecoles_gerees', None):
            ecoles.extend(current_user.ecoles_gerees)
        # Ã©liminer doublons
        ecoles = list({e.id: e for e in ecoles}.values())
    
    # Pour super-admin : toutes les Ã©coles
    else:
        ecoles = get_ecole_filter_query(Ecole).all()
    
    # PrÃ©parer les donnÃ©es de journaux et problÃ¨mes pour chaque Ã©cole
    for ecole in ecoles:
        # journaux_correction et problemes doivent Ãªtre des relations SQLAlchemy
        ecole.journaux_correction = getattr(ecole, 'journaux_correction', [])
        ecole.problemes = getattr(ecole, 'problemes', [])

    return render_template('choisir_ecole.html', ecoles=ecoles)

@main.route('/admin/ecoles')
@login_required
@role_required('super_admin')
def gestion_ecoles():
    """Gestion des écoles (super-admin seulement)"""
    try:
        from app.models import Classe, Eleve, Professeur
        ecoles = get_ecole_filter_query(Ecole).order_by(Ecole.id.desc()).all()
        total_eleves = 0
        for ecole in ecoles:
            ecole.nb_eleves = Eleve.query.filter_by(ecole_id=ecole.id).count()
            ecole.nb_classes = Classe.query.filter_by(ecole_id=ecole.id).count()
            ecole.nb_profs = Professeur.query.filter_by(ecole_id=ecole.id).count()
            total_eleves += ecole.nb_eleves
            if not ecole.email:
                admin_user = Utilisateur.query.filter_by(ecole_id=ecole.id, role='admin').first()
                if admin_user and admin_user.email:
                    ecole.email = admin_user.email

        stats = {
            'total_ecoles': len(ecoles),
            'ecoles_actives': sum(1 for e in ecoles if e.statut in ('actif', 'active')),
            'ecoles_bloquees': sum(1 for e in ecoles if e.statut in ('bloque', 'suspendu', 'inactive')),
            'total_eleves': total_eleves,
        }
        return render_template('admin/ecoles.html', ecoles=ecoles, stats=stats)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur récupération écoles : {e}")
        flash("Erreur lors de la récupération des écoles.", "danger")
        return render_template('admin/ecoles.html', ecoles=[], stats={
            'total_ecoles': 0, 'ecoles_actives': 0, 'ecoles_bloquees': 0, 'total_eleves': 0
        })

@main.route('/admin/ecoles/ajouter', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def ajouter_ecole():
    """Ajouter une nouvelle ecole et son administrateur"""
    if request.method == 'POST':
        try:
            nom_ecole    = request.form.get('nom_ecole', '').strip()
            adresse      = request.form.get('adresse', '').strip()
            telephone    = request.form.get('telephone', '').strip()
            email_admin  = request.form.get('email_admin', '').strip()
            mot_de_passe = request.form.get('mot_de_passe', '').strip() or secrets.token_urlsafe(12)

            if not nom_ecole:
                flash("Le nom de l'école est obligatoire.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            if not email_admin:
                flash("L'email de l'administrateur est obligatoire.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            if Utilisateur.query.filter_by(email=email_admin).first():
                flash("Cet email est déjà utilisé par un autre utilisateur.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            # Création école (un seul nom et un seul email)
            ecole = Ecole(
                nom=nom_ecole,
                adresse=adresse,
                telephone=telephone,
                email=email_admin,
                statut='active'
            )
            db.session.add(ecole)
            db.session.flush()

            # Création admin lié à l'école
            admin = Utilisateur(
                nom=nom_ecole,
                email=email_admin,
                role='admin',
                mot_de_passe=generate_password_hash(mot_de_passe),
                ecole_id=ecole.id,
                statut='actif'
            )
            db.session.add(admin)
            db.session.commit()

            # Email de bienvenue (optionnel)
            try:
                sujet = f"Bienvenue sur KLASORA - {ecole.nom}"
                corps = (
                    f"Votre école \"{ecole.nom}\" a été créée sur KLASORA.\n\n"
                    f"Email : {admin.email}\n"
                    f"Mot de passe : {mot_de_passe}\n"
                )
                envoyer_email(admin.email, sujet, corps)
            except Exception as mail_err:
                current_app.logger.warning(f"Email non envoyé : {mail_err}")

            flash(f"École « {ecole.nom} » créée avec succès ✅", "success")
            return redirect(url_for('main.gestion_ecoles'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur création école : {e}")
            flash("Erreur lors de la création de l'école.", "danger")

    return render_template('admin/ajouter_ecole.html')

@main.route('/api/ecoles')
@login_required
def api_ecoles():
    """API pour rÃ©cupÃ©rer les Ã©coles accessibles"""
    try:
        if current_user.role == 'super_admin':
            ecoles = get_ecole_filter_query(Ecole).all()
        else:
            ecoles = []
            if getattr(current_user, 'ecole', None):
                ecoles.append(current_user.ecole)
            if getattr(current_user, 'ecoles_gerees', None):
                ecoles.extend(current_user.ecoles_gerees)
            # Ã©liminer doublons
            ecoles = list({e.id: e for e in ecoles}.values())

        return jsonify([{'id': e.id, 'nom': e.nom} for e in ecoles])
    except Exception as e:
        current_app.logger.error(f"Erreur API Ã©coles : {e}")
        return jsonify([]), 500

@main.route('/admin/ecoles/<int:ecole_id>/assigner', methods=['POST'])
@login_required
@role_required('super_admin')
def assigner_ecole(ecole_id):
    """Assigner une Ã©cole Ã  un gestionnaire"""
    try:
        utilisateur_id = int(request.form.get('utilisateur_id'))
        utilisateur = Utilisateur.query.get_or_404(utilisateur_id)
        ecole = Ecole.query.get_or_404(ecole_id)

        # Supprimer association existante si nÃ©cessaire
        db.session.execute(
            gestion_ecole.delete().where(
                (gestion_ecole.c.utilisateur_id == utilisateur_id) &
                (gestion_ecole.c.ecole_id == ecole_id)
            )
        )

        # Ajouter association
        db.session.execute(
            gestion_ecole.insert().values(
                utilisateur_id=utilisateur_id,
                ecole_id=ecole_id
            )
        )
        db.session.commit()
        flash(f"Ã‰cole '{ecole.nom}' assignÃ©e Ã  {utilisateur.prenom} {utilisateur.nom}", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur assignation Ã©cole : {e}")
        flash("Erreur lors de l'assignation de l'Ã©cole.", "danger")
    return redirect(request.referrer or url_for('main.gestion_ecoles'))

@main.route('/admin/utilisateur/<int:user_id>/ecoles', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def gerer_ecoles_utilisateur(user_id):
    utilisateur = Utilisateur.query.get_or_404(user_id)
    toutes_ecoles = get_ecole_filter_query(Ecole).all()
    form = GererEcolesForm()

    ecoles_actuelles = [e.id for e in getattr(utilisateur, 'ecoles_gerees', [])]

    if form.validate_on_submit():
        try:
            ecoles_selectionnees = [int(ecole_id) for ecole_id in request.form.getlist('ecoles')]
            ecoles_decocher = set(ecoles_actuelles) - set(ecoles_selectionnees)

            erreurs = []
            for ecole_id in ecoles_decocher:
                nb_eleves = Eleve.query.filter_by(ecole_id=ecole_id).count()
                if nb_eleves > 0:
                    ecole = Ecole.query.get(ecole_id)
                    erreurs.append(f"L'Ã©cole '{ecole.nom}' contient encore {nb_eleves} Ã©lÃ¨ves et ne peut pas Ãªtre retirÃ©e.")

            if erreurs:
                for err in erreurs:
                    flash(err, 'danger')
                return redirect(url_for('main.gerer_ecoles_utilisateur', user_id=user_id))

            # Supprimer associations existantes
            db.session.execute(gestion_ecole.delete().where(gestion_ecole.c.utilisateur_id == user_id))

            # Ajouter nouvelles associations
            for ecole_id in ecoles_selectionnees:
                db.session.execute(gestion_ecole.insert().values(
                    utilisateur_id=user_id,
                    ecole_id=ecole_id
                ))

            db.session.commit()
            flash("Ã‰coles assignÃ©es avec succÃ¨s", "success")
            return redirect(url_for('main.gestion_utilisateurs'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur gestion Ã©coles utilisateur : {e}")
            flash(f"Erreur : {str(e)}", "danger")

    # Statistiques globales
    nb_ecoles = Ecole.query.count()
    nb_eleves = Eleve.query.count()
    nb_parents = Utilisateur.query.filter_by(role='parent').count()
    nb_enseignants = Utilisateur.query.filter_by(role='professeur').count()
    nb_admins = Utilisateur.query.filter_by(role='admin').count()

    stats_ecoles = []
    for ecole in toutes_ecoles:
        nb_eleve = Eleve.query.filter_by(ecole_id=ecole.id).count()
        stats_ecoles.append({
            'id': ecole.id,
            'nom': ecole.nom,
            'eleves': nb_eleve
        })

    return render_template(
        "admin/gerer_ecoles_utilisateur.html",
        utilisateur=utilisateur,
        toutes_ecoles=toutes_ecoles,
        ecoles_actuelles=ecoles_actuelles,
        form=form,
        nb_ecoles=nb_ecoles,
        nb_eleves=nb_eleves,
        nb_parents=nb_parents,
        nb_enseignants=nb_enseignants,
        nb_admins=nb_admins,
        stats_ecoles=stats_ecoles
    )

def safe_delete_ecole(ecole_id):
    """Supprime proprement et en cascade toutes les données liées à une école"""
    from app.models import (
        Absence, Alerte, AnneeScolaire, ArchiveAbsence, ArchiveNote,
        Bulletin, Classe, Cours, Eleve, EmploiTemps, HistoriqueImport,
        Inscription, JournalCorrection, Log, Note, Paiement,
        PeriodeBulletin, Presence, Professeur, Utilisateur
    )
    ecole = Ecole.query.get_or_404(ecole_id)
    nom_ecole = ecole.nom

    # 1. IDs des élèves et utilisateurs non super-admin
    eleves = Eleve.query.filter_by(ecole_id=ecole_id).all()
    eleve_ids = [el.id for el in eleves]

    users = Utilisateur.query.filter_by(ecole_id=ecole_id).all()
    user_ids = [u.id for u in users if u.role != 'super_admin']

    # 2. Données liées aux élèves
    if eleve_ids:
        Presence.query.filter(Presence.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Bulletin.query.filter(Bulletin.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Inscription.query.filter(Inscription.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        ArchiveNote.query.filter(ArchiveNote.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        ArchiveAbsence.query.filter(ArchiveAbsence.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Note.query.filter(Note.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Absence.query.filter(Absence.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)
        Paiement.query.filter(Paiement.eleve_id.in_(eleve_ids)).delete(synchronize_session=False)

    # 3. Données scolaires directes
    Note.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Absence.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Paiement.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    EmploiTemps.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    PeriodeBulletin.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    JournalCorrection.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Log.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

    # 4. Tables de liaison n-m
    for table_query in [
        "DELETE FROM professeur_classes WHERE ecole_id = :eid",
        "DELETE FROM ecole_cours WHERE ecole_id = :eid",
        "DELETE FROM gestion_ecole WHERE ecole_id = :eid"
    ]:
        try:
            db.session.execute(db.text(table_query), {'eid': ecole_id})
        except Exception as e:
            current_app.logger.debug(f"Nettoyage association ({table_query}): {e}")

    # 5. Cours, Élèves, Classes, Années, Professeurs
    Cours.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Eleve.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Classe.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    AnneeScolaire.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
    Professeur.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

    # 6. Données liées aux utilisateurs de l'école
    if user_ids:
        HistoriqueImport.query.filter(HistoriqueImport.utilisateur_id.in_(user_ids)).delete(synchronize_session=False)
        Alerte.query.filter(Alerte.utilisateur_id.in_(user_ids)).delete(synchronize_session=False)
        Log.query.filter(Log.utilisateur_id.in_(user_ids)).delete(synchronize_session=False)

    # 7. Utilisateurs non-superadmin
    Utilisateur.query.filter(
        Utilisateur.ecole_id == ecole_id,
        Utilisateur.role != 'super_admin'
    ).delete(synchronize_session=False)

    # 8. Nettoyer la session courante si nécessaire
    if session.get('ecole_id') == ecole_id:
        autre_ecole = Ecole.query.filter(Ecole.id != ecole_id).first()
        session['ecole_id'] = autre_ecole.id if autre_ecole else None

    # 9. Supprimer l'école elle-même
    db.session.delete(ecole)
    db.session.commit()
    return nom_ecole


@main.route('/admin/ecoles/<int:ecole_id>/modifier', methods=['POST'])
@login_required
@role_required('super_admin')
def modifier_ecole(ecole_id):
    """Modifier les informations d'un établissement (un seul nom, un seul email)"""
    ecole = Ecole.query.get_or_404(ecole_id)
    try:
        nom = request.form.get('nom', '').strip()
        email = request.form.get('email', '').strip()
        telephone = request.form.get('telephone', '').strip()
        adresse = request.form.get('adresse', '').strip()

        if not nom:
            flash("Le nom de l'école est obligatoire.", "danger")
            return redirect(url_for('main.gestion_ecoles'))

        if not email:
            flash("L'email de l'école est obligatoire.", "danger")
            return redirect(url_for('main.gestion_ecoles'))

        admin = Utilisateur.query.filter_by(ecole_id=ecole.id, role='admin').first()

        # Vérifier unicité email si modifié
        conflit = Utilisateur.query.filter(
            Utilisateur.email == email,
            Utilisateur.id != (admin.id if admin else None)
        ).first()
        if conflit:
            flash("Cet email est déjà utilisé par un autre compte utilisateur.", "danger")
            return redirect(url_for('main.gestion_ecoles'))

        # Mise à jour école
        ecole.nom = nom
        ecole.email = email
        ecole.telephone = telephone or None
        ecole.adresse = adresse or None

        # Synchronisation de l'administrateur de l'école
        if admin:
            admin.email = email
            admin.nom = nom

        db.session.commit()
        flash(f"L'école « {ecole.nom} » a été modifiée avec succès ✅", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur modification école {ecole_id}: {e}")
        flash("Une erreur est survenue lors de la modification de l'école.", "danger")

    return redirect(url_for('main.gestion_ecoles'))


@main.route('/admin/ecoles/<int:ecole_id>/bloquer', methods=['POST'])
@login_required
@role_required('super_admin')
def bloquer_ecole(ecole_id):
    """Bloquer ou débloquer un établissement avec motif explicite"""
    ecole = Ecole.query.get_or_404(ecole_id)
    action = request.form.get('action', '').strip()  # 'bloquer' ou 'debloquer'
    motif = request.form.get('motif_blocage', '').strip()

    try:
        if action == 'bloquer':
            ecole.statut = 'bloque'
            ecole.motif_blocage = motif or 'Suspension administrative'
            db.session.commit()
            flash(f"L'école « {ecole.nom} » a été bloquée 🛑 (Motif: {ecole.motif_blocage})", "warning")
        elif action == 'debloquer':
            ecole.statut = 'actif'
            ecole.motif_blocage = None
            db.session.commit()
            flash(f"L'école « {ecole.nom} » a été débloquée et réactivée avec succès 🟢", "success")
        else:
            flash("Action non reconnue.", "danger")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur changement statut école {ecole_id}: {e}")
        flash("Une erreur est survenue lors de l'opération sur l'école.", "danger")

    return redirect(url_for('main.gestion_ecoles'))


@main.route('/admin/ecoles/<int:ecole_id>/supprimer', methods=['POST'])
@login_required
@role_required('super_admin')
def supprimer_ecole_action(ecole_id):
    """Supprimer définitivement une école et toutes ses données associées (via formulaire)"""
    try:
        nom_ecole = safe_delete_ecole(ecole_id)
        flash(f"L'école « {nom_ecole} » et toutes ses données associées ont été supprimées définitivement 🗑️", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression école {ecole_id}: {e}")
        flash(f"Erreur lors de la suppression de l'école : {str(e)}", "danger")

    return redirect(url_for('main.gestion_ecoles'))


@main.route('/api/ecoles/<int:ecole_id>', methods=['DELETE'])
@login_required
@role_required('super_admin')
def supprimer_ecole(ecole_id):
    """Supprimer une école et tous ses utilisateurs associés (via API DELETE)"""
    try:
        nom_ecole = safe_delete_ecole(ecole_id)
        return jsonify({'success': True, 'message': f"L'école « {nom_ecole} » a été supprimée avec succès."})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur lors de la suppression: {str(e)}'}), 500


@main.route('/api/ecoles/<int:ecole_id>/status', methods=['PUT'])
@login_required
@role_required('super_admin')
def toggle_ecole_status(ecole_id):
    """Changer le statut d'une école (API bascule active/inactive)"""
    if current_user.role != 'super_admin':
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    ecole = Ecole.query.get_or_404(ecole_id)
    ecole.statut = 'inactive' if ecole.statut in ('active', 'actif') else 'actif'
    db.session.commit()

    return jsonify({'success': True, 'new_status': ecole.statut})

