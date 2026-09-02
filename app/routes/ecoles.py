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
    envoyer_telegram,
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
    url_for,
)


@main.route('/choisir-ecole')
@login_required
def choisir_ecole():
    """Afficher automatiquement les écoles accessibles et leurs journaux/problèmes"""
    
    # Pour un admin normal : récupérer son école et ses écoles gérées
    if current_user.role != "super_admin":
        ecoles = []
        if current_user.ecole:
            ecoles.append(current_user.ecole)
        if getattr(current_user, 'ecoles_gerees', None):
            ecoles.extend(current_user.ecoles_gerees)
        # éliminer doublons
        ecoles = list({e.id: e for e in ecoles}.values())
    
    # Pour super-admin : toutes les écoles
    else:
        ecoles = get_ecole_filter_query(Ecole).all()
    
    # Préparer les données de journaux et problèmes pour chaque école
    for ecole in ecoles:
        # journaux_correction et problemes doivent être des relations SQLAlchemy
        ecole.journaux_correction = getattr(ecole, 'journaux_correction', [])
        ecole.problemes = getattr(ecole, 'problemes', [])

    return render_template('choisir_ecole.html', ecoles=ecoles)

@main.route('/admin/ecoles')
@login_required
@role_required('super_admin')
def gestion_ecoles():
    """Gestion des écoles (super-admin seulement)"""
    try:
        ecoles = get_ecole_filter_query(Ecole).all()
        return render_template('admin/ecoles.html', ecoles=ecoles)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur récupération écoles : {e}")
        flash("Erreur lors de la récupération des écoles.", "danger")
        return render_template('admin/ecoles.html', ecoles=[])

@main.route('/admin/ecoles/ajouter', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def ajouter_ecole():
    """Ajouter une nouvelle école et son administrateur associé"""
    if request.method == 'POST':
        try:
            # Récupérer et valider les champs
            nom_ecole = request.form.get('nom_ecole', '').strip()
            adresse = request.form.get('adresse', '').strip()
            telephone = request.form.get('telephone', '').strip()
            email_ecole = request.form.get('email', '').strip()
            directeur = request.form.get('directeur', '').strip()
            nom_admin = request.form.get('nom_admin', '').strip()
            prenom_admin = request.form.get('prenom_admin', '').strip()
            email_admin = request.form.get('email_admin', '').strip()
            telephone_admin = request.form.get('telephone_admin', '').strip()
            mot_de_passe = request.form.get('mot_de_passe') or secrets.token_urlsafe(12)  # + sécurisé

            # Vérifier doublon email admin
            if Utilisateur.query.filter_by(email=email_admin).first():
                flash("Cet email est déjà utilisé pour un autre utilisateur.", "danger")
                return redirect(url_for('main.ajouter_ecole'))

            # --- Création de l'école ---
            ecole = Ecole(
                nom=nom_ecole,
                adresse=adresse,
                telephone=telephone,
                email=email_ecole,
                directeur=directeur,
                statut='active'
            )
            db.session.add(ecole)
            db.session.flush()  # pour obtenir ecole.id avant commit

            # --- Création de l'admin associé ---
            admin = Utilisateur(
                nom=nom_admin,
                prenom=prenom_admin,
                email=email_admin,
                telephone=telephone_admin,
                role='admin',
                mot_de_passe=generate_password_hash(mot_de_passe),
                ecole_id=ecole.id,
                statut='actif'
            )
            db.session.add(admin)
            db.session.commit()

            # --- Notifications ---
            sujet = f"Bienvenue sur EduManage - {ecole.nom}"
            message = f"""
Bonjour {admin.prenom} {admin.nom},

Votre école "{ecole.nom}" a été créée avec succès sur EduManage 🎉

Identifiants de connexion :
📧 Email : {admin.email}
🔑 Mot de passe : {mot_de_passe}

Merci d'utiliser notre plateforme !
"""
            try:
                envoyer_email(admin.email, sujet, message)
            except Exception as e:
                current_app.logger.warning(f"Échec envoi email : {e}")

            try:
                telegram_message = f"""
🏫 Nouvelle école créée : {ecole.nom}
👤 Admin : {admin.prenom} {admin.nom}
📧 {admin.email}
🔑 {mot_de_passe}
"""
                envoyer_telegram(telegram_message)
            except Exception as e:
                current_app.logger.warning(f"Échec envoi Telegram : {e}")

            flash("École et administrateur créés avec succès ✅", "success")
            return redirect(url_for('main.gestion_ecoles'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur création école+admin : {e}")
            flash("Erreur lors de la création de l'école et de son admin.", "danger")

    return render_template('admin/ajouter_ecole.html')

@main.route('/api/ecoles')
@login_required
def api_ecoles():
    """API pour récupérer les écoles accessibles"""
    try:
        if current_user.role == 'super_admin':
            ecoles = get_ecole_filter_query(Ecole).all()
        else:
            ecoles = []
            if getattr(current_user, 'ecole', None):
                ecoles.append(current_user.ecole)
            if getattr(current_user, 'ecoles_gerees', None):
                ecoles.extend(current_user.ecoles_gerees)
            # éliminer doublons
            ecoles = list({e.id: e for e in ecoles}.values())

        return jsonify([{'id': e.id, 'nom': e.nom} for e in ecoles])
    except Exception as e:
        current_app.logger.error(f"Erreur API écoles : {e}")
        return jsonify([]), 500

@main.route('/admin/ecoles/<int:ecole_id>/assigner', methods=['POST'])
@login_required
@role_required('super_admin')
def assigner_ecole(ecole_id):
    """Assigner une école à un gestionnaire"""
    try:
        utilisateur_id = int(request.form.get('utilisateur_id'))
        utilisateur = Utilisateur.query.get_or_404(utilisateur_id)
        ecole = Ecole.query.get_or_404(ecole_id)

        # Supprimer association existante si nécessaire
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
        flash(f"École '{ecole.nom}' assignée à {utilisateur.prenom} {utilisateur.nom}", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur assignation école : {e}")
        flash("Erreur lors de l'assignation de l'école.", "danger")
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
            decochées = set(ecoles_actuelles) - set(ecoles_selectionnees)

            erreurs = []
            for ecole_id in decochées:
                nb_eleves = Eleve.query.filter_by(ecole_id=ecole_id).count()
                if nb_eleves > 0:
                    ecole = Ecole.query.get(ecole_id)
                    erreurs.append(f"L'école '{ecole.nom}' contient encore {nb_eleves} élèves et ne peut pas être retirée.")

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
            flash("Écoles assignées avec succès", "success")
            return redirect(url_for('main.gestion_utilisateurs'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur gestion écoles utilisateur : {e}")
            flash(f"Erreur : {str(e)}", "danger")

    # Statistiques globales
    nb_ecoles = Ecole.query.count()
    nb_eleves = Eleve.query.count()
    nb_parents = Utilisateur.query.filter_by(role='parent').count()
    nb_enseignants = Utilisateur.query.filter_by(role='enseignant').count()
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

@main.route('/api/ecoles/<int:ecole_id>/status', methods=['PUT'])
@login_required
@role_required('super_admin')
def toggle_ecole_status(ecole_id):
    """Changer le statut d'une école"""
    if current_user.role != 'super_admin':
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403
        
    ecole = Ecole.query.get_or_404(ecole_id)
    ecole.statut = 'inactive' if ecole.statut == 'active' else 'active'
    db.session.commit()
    
    return jsonify({'success': True, 'new_status': ecole.statut})

@main.route('/api/ecoles/<int:ecole_id>', methods=['DELETE'])
@login_required
@role_required('super_admin')
def supprimer_ecole(ecole_id):
    """Supprimer une école et tous ses utilisateurs associés"""
    ecole = Ecole.query.get_or_404(ecole_id)
    
    try:
        # Supprimer tous les utilisateurs liés
        for user in ecole.utilisateurs:
            # Supprimer les enfants et inscriptions
            for enfant in user.get_enfants():
                for inscription in enfant.inscriptions:
                    db.session.delete(inscription)
                db.session.delete(enfant)
            
            # Supprimer les cours si c'est un professeur
            if user.professeur_rel:
                for cours in user.cours_enseignes:
                    cours.enseignant_id = None
                db.session.delete(user.professeur_rel)
            
            # Supprimer alertes et logs
            for alerte in user.alertes:
                db.session.delete(alerte)
            for log in user.logs:
                db.session.delete(log)
            
            db.session.delete(user)
        
        # Supprimer l'école
        db.session.delete(ecole)
        db.session.commit()
        return jsonify({'success': True, 'message': 'École et utilisateurs supprimés avec succès.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur lors de la suppression: {str(e)}'}), 500
