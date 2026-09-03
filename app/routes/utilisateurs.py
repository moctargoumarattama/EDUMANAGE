from . import main
from .common import (
    CreateUserForm,
    Ecole,
    Eleve,
    IntegrityError,
    JournalCorrection,
    Professeur,
    User,
    Utilisateur,
    ajouter_ecole_id,
    bcrypt,
    current_app,
    current_user,
    datetime,
    db,
    filtre_par_ecole,
    flash,
    get_ecole_courante,
    get_ecole_filter_query,
    joinedload,
    jsonify,
    login_required,
    redirect,
    render_template,
    request,
    role_required,
    timedelta,
    url_for,
)


@main.route('/admin/create_user', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def create_user():
    """CrÃ©ation d'utilisateurs par l'administrateur"""
    form = CreateUserForm()
    form.eleve_id.choices = [(0, "--- Aucun ---")] + [(e.id, f"{e.nom} {e.prenom} ({e.classe})") for e in get_ecole_filter_query(Eleve).all()]

    if form.validate_on_submit():
        try:
            if form.role.data != 'admin':
                flash("La creation directe est limitee aux comptes admin.", "warning")
                return redirect(url_for('main.create_user'))

            hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
            user = Utilisateur(
                nom=form.nom.data,
                email=form.email.data,
                mot_de_passe=hashed_password,
                role=form.role.data
            )
            
            if form.role.data == 'parent' and form.eleve_id.data != 0:
                user.eleve_id = form.eleve_id.data

            db.session.add(user)
            db.session.commit()
            flash(f"Utilisateur {user.nom} crÃ©Ã© avec succÃ¨s !", "success")
            return redirect(url_for('main.dashboard'))
            
        except IntegrityError as e:
            db.session.rollback()
            if 'email' in str(e):
                flash("Cet email est dÃ©jÃ  utilisÃ©.", "danger")
            else:
                flash("Erreur lors de la crÃ©ation de l'utilisateur.", "danger")
                current_app.logger.error(f"IntegrityError: {e}")
        except Exception as e:
            db.session.rollback()
            flash("Erreur inattendue lors de la crÃ©ation.", "danger")
            current_app.logger.error(f"Erreur crÃ©ation utilisateur: {e}")

    return render_template('admin/create_user.html', form=form)

@main.route('/admin/utilisateurs')
@login_required
@role_required('admin')
def gestion_utilisateurs():
    if current_user.role == 'super_admin':
        return redirect(url_for('main.gestion_ecoles'))

    page = request.args.get('page', 1, type=int)
    ecole = get_ecole_courante()

    try:
        if not ecole:
            flash("Votre compte n'est associÃ© Ã  aucune Ã©cole.", "danger")
            return redirect(url_for('main.index'))

        utilisateurs_query = filtre_par_ecole(Utilisateur.query, Utilisateur)
        professeurs_query = filtre_par_ecole(Professeur.query, Professeur)
        parents_query = filtre_par_ecole(Utilisateur.query.filter_by(role='parent'), Utilisateur)

        utilisateurs = utilisateurs_query.paginate(page=page, per_page=10, error_out=False)
        professeurs = professeurs_query.paginate(page=page, per_page=10, error_out=False)
        parents = parents_query.paginate(page=page, per_page=10, error_out=False)

        return render_template(
            'profile.html',
            utilisateurs=utilisateurs,
            professeurs=professeurs,
            parents=parents
        )

    except Exception as e:
        current_app.logger.error(f"Erreur gestion utilisateurs: {e}")
        flash("Erreur lors de la rÃ©cupÃ©ration des utilisateurs.", "danger")
        return redirect(url_for('main.index'))

@main.route('/admin/creer_utilisateur', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def creer_utilisateur():
    from werkzeug.security import generate_password_hash

    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        email = request.form.get('email', '').strip()
        telephone = request.form.get('telephone', '').strip()
        role = 'admin'
        mot_de_passe = request.form.get('mot_de_passe', '').strip()

        if not all([nom, prenom, email, mot_de_passe]):
            flash("Tous les champs obligatoires doivent Ãªtre remplis.", "warning")
            return redirect(url_for('main.creer_utilisateur'))

        if Utilisateur.query.filter_by(email=email).first():
            flash('Cet email est dÃ©jÃ  utilisÃ©', 'danger')
            return redirect(url_for('main.gestion_utilisateurs'))

        try:
            nouvel_utilisateur = Utilisateur(
                nom=nom,
                prenom=prenom,
                email=email,
                telephone=telephone,
                role=role,
                mot_de_passe=generate_password_hash(mot_de_passe),
                statut='actif'
            )

            ajouter_ecole_id(nouvel_utilisateur)

            db.session.add(nouvel_utilisateur)
            db.session.commit()

            flash('Utilisateur crÃ©Ã© avec succÃ¨s', 'success')
            return redirect(url_for('main.gestion_utilisateurs'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur crÃ©ation utilisateur: {e}")
            flash("Erreur lors de la crÃ©ation de l'utilisateur.", "danger")
            return redirect(url_for('main.gestion_utilisateurs'))

    return render_template('admin/creer_utilisateur.html')

@main.route('/admin/utilisateur/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_utilisateur(id):
    if current_user.role == 'super_admin':
        return redirect(url_for('main.gestion_ecoles'))

    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=id).first_or_404()
    roles_autorises = ['admin', 'parent', 'professeur', 'enseignant']

    if request.method == 'POST':
        role = request.form.get('role', '').strip()
        statut = request.form.get('statut', '').strip() or 'actif'
        email = request.form.get('email', '').strip().lower()

        if role not in roles_autorises:
            flash("RÃ´le utilisateur invalide.", "danger")
            return redirect(url_for('main.modifier_utilisateur', id=user.id))

        doublon = Utilisateur.query.filter(Utilisateur.email == email, Utilisateur.id != user.id).first()
        if doublon:
            flash("Cet email est dÃ©jÃ  utilisÃ©.", "danger")
            return redirect(url_for('main.modifier_utilisateur', id=user.id))

        user.nom = request.form.get('nom', user.nom).strip()
        user.prenom = request.form.get('prenom', user.prenom).strip()
        user.email = email
        user.telephone = request.form.get('telephone', '').strip() or None
        user.role = role
        user.statut = statut

        password = request.form.get('password', '').strip()
        if password:
            user.mot_de_passe = bcrypt.generate_password_hash(password).decode('utf-8')

        db.session.commit()
        flash("Utilisateur modifiÃ© avec succÃ¨s.", "success")
        return redirect(url_for('main.gestion_utilisateurs'))

    return render_template('edit_utilisateur.html', user=user, roles=roles_autorises)

@main.route('/admin/utilisateur/<int:user_id>/statut', methods=['POST'])
@login_required
@role_required('admin')
def changer_statut_utilisateur(user_id):
    try:
        user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()
        data = request.get_json()
        if not data or 'statut' not in data:
            return jsonify({'success': False, 'message': 'DonnÃ©es JSON requises'}), 400

        nouveau_statut = data.get('statut')
        if nouveau_statut not in ['actif', 'bloque']:
            return jsonify({'success': False, 'message': 'Statut invalide'}), 400

        user.statut = nouveau_statut
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur changement statut utilisateur: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/admin/utilisateur/<int:user_id>', methods=['DELETE'])
@login_required
@role_required('admin')
def supprimer_utilisateur(user_id):
    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()

    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer vous-mÃªme'}), 403

    try:
        db.session.delete(user)
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression utilisateur: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/admin/eleve/<int:eleve_id>/regenerer-code', methods=['POST'])
@login_required
@role_required('admin')
def regenerer_code_parent(eleve_id):
    eleve = filtre_par_ecole(Eleve.query, Eleve).filter_by(id=eleve_id).first_or_404()
    try:
        nouveau_code = Eleve.generer_code_parent()
        eleve.code_parent = nouveau_code
        db.session.commit()
        return jsonify({'success': True, 'nouveau_code': nouveau_code}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur gÃ©nÃ©ration code parent: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/admin/parent/<int:parent_id>/envoyer-credentials', methods=['POST'])
@login_required
@role_required('admin')
def envoyer_credentials_parent(parent_id):
    parent = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=parent_id, role='parent').first()
    if not parent:
        return jsonify({'success': False, 'message': "Parent introuvable ou non autorisÃ©."}), 404

    eleve = parent.enfants[0] if parent.enfants else None
    if not eleve:
        return jsonify({'success': False, 'message': "Aucun Ã©lÃ¨ve associÃ© Ã  ce parent."}), 400

    if not eleve.code_parent:
        eleve.code_parent = Eleve.generer_code_parent()
        db.session.commit()

    try:
        import qrcode, io, base64
        qr_data = f"Parent: {parent.prenom} {parent.nom}\nEmail: {parent.email}\nMot de passe: {eleve.code_parent}"
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        qr_base64 = base64.b64encode(buffer.getvalue()).decode()

        sujet = "Vos identifiants de connexion - EduManage"
        message = f"""
        <h3>Bonjour {parent.prenom or ''} {parent.nom},</h3>
        <p>Voici vos identifiants pour accÃ©der au portail parent :</p>
        <ul>
            <li>Email : {parent.email}</li>
            <li>Code d'accÃ¨s : {eleve.code_parent}</li>
        </ul>
        <img src="data:image/png;base64,{qr_base64}" width="150" height="150"/>
        """

        from app.notifications import envoyer_email
        if envoyer_email(parent.email, sujet, message):
            return jsonify({'success': True, 'message': 'Email envoyÃ© avec succÃ¨s.'}), 200
        else:
            return jsonify({'success': False, 'message': 'Erreur lors de lâ€™envoi de lâ€™email.'}), 500

    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Erreur lors de lâ€™envoi des credentials: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/journaux_corrections', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
def journaux_corrections():
    # RÃ©cupÃ©ration des Ã©coles et utilisateurs accessibles
    toutes_ecoles = get_ecole_filter_query(Ecole).all()
    tous_users = get_ecole_filter_query(User).all()

    # RÃ©cupÃ©ration des filtres
    ecole_id = request.args.get('ecole_id', type=int)
    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action', '').strip()
    niveau = request.args.get('niveau', '').strip()
    date_debut = request.args.get('date_debut')
    date_fin = request.args.get('date_fin')

    # Construire la requÃªte avec prÃ©-chargement
    query = JournalCorrection.query.options(
        joinedload(JournalCorrection.ecole),
        joinedload(JournalCorrection.user)
    )

    # Filtrage multi-Ã©coles si l'utilisateur n'est pas super_admin
    if current_user.role != 'super_admin':
        ecoles_accessibles = [e.id for e in getattr(current_user, 'ecoles_gerees', [])]
        if getattr(current_user, 'ecole', None):
            ecoles_accessibles.append(current_user.ecole.id)
        query = query.filter(JournalCorrection.ecole_id.in_(ecoles_accessibles))

    # Application des filtres
    if ecole_id:
        query = query.filter(JournalCorrection.ecole_id == ecole_id)
    if user_id:
        query = query.filter(JournalCorrection.user_id == user_id)
    if action:
        query = query.filter(JournalCorrection.action.ilike(f"%{action}%"))
    if niveau:
        query = query.filter(JournalCorrection.niveau == niveau)
    if date_debut:
        try:
            dt_start = datetime.strptime(date_debut, "%Y-%m-%d")
            query = query.filter(JournalCorrection.date >= dt_start)
        except ValueError:
            flash("Format de date de dÃ©but invalide", "warning")
    if date_fin:
        try:
            dt_end = datetime.strptime(date_fin, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(JournalCorrection.date < dt_end)
        except ValueError:
            flash("Format de date de fin invalide", "warning")

    # RÃ©cupÃ©ration des corrections
    corrections = query.order_by(JournalCorrection.date.desc()).all()

    return render_template(
        "journaux_corrections.html",
        corrections=corrections,
        toutes_ecoles=toutes_ecoles,
        tous_users=tous_users,
        filtre={
            "ecole_id": ecole_id,
            "user_id": user_id,
            "action": action,
            "niveau": niveau,
            "date_debut": date_debut,
            "date_fin": date_fin
        }
    )

@main.route('/api/users/<int:user_id>/status', methods=['PUT'])
@login_required
def toggle_user_status(user_id):
    """Changer le statut d'un utilisateur"""
    if current_user.role not in ['admin']:
        return jsonify({'success': False, 'message': 'Non autorisÃ©'}), 403
        
    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()
    
    # VÃ©rifier les permissions
    if user.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisÃ©'}), 403
        
    # EmpÃªcher de se dÃ©sactiver soi-mÃªme
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas modifier votre propre statut'}), 400
    
    user.statut = 'bloque' if user.statut == 'actif' else 'actif'
    db.session.commit()
    
    return jsonify({'success': True, 'new_status': user.statut})

@main.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """Supprimer un utilisateur et toutes ses dÃ©pendances (enfants + inscriptions)"""
    
    # VÃ©rification des rÃ´les
    if current_user.role not in ['admin']:
        return jsonify({'success': False, 'message': 'Non autorisÃ©'}), 403

    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()

    # EmpÃªcher un admin de supprimer un utilisateur d'une autre Ã©cole
    if user.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisÃ©'}), 403

    # EmpÃªcher de se supprimer soi-mÃªme
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer'}), 400

    try:
        # 1ï¸âƒ£ Supprimer les inscriptions des enfants
        for enfant in user.get_enfants():
            for inscription in enfant.inscriptions:
                db.session.delete(inscription)

        # 2ï¸âƒ£ Supprimer les enfants
        for enfant in user.get_enfants():
            db.session.delete(enfant)

        # 3ï¸âƒ£ Supprimer les relations professeur si existantes
        if user.professeur_rel:
            # Supprimer les cours enseignÃ©s par ce professeur si nÃ©cessaire
            for cours in user.cours_enseignes:
                cours.enseignant_id = None  # ou supprimer si tu veux
            db.session.delete(user.professeur_rel)

        # 4ï¸âƒ£ Supprimer alertes et logs
        for alerte in user.alertes:
            db.session.delete(alerte)
        for log in user.logs:
            db.session.delete(log)

        # 5ï¸âƒ£ Supprimer lâ€™utilisateur
        db.session.delete(user)

        db.session.commit()
        return jsonify({'success': True, 'message': 'Utilisateur supprimÃ© avec toutes ses dÃ©pendances.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur lors de la suppression: {str(e)}'}), 500

