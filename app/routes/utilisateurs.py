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
    """Création d'utilisateurs par l'administrateur"""
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
            flash(f"Utilisateur {user.nom} créé avec succès !", "success")
            return redirect(url_for('main.dashboard'))

        except IntegrityError as e:
            db.session.rollback()
            if 'email' in str(e):
                flash("Cet email est d?j? utilisé.", "danger")
            else:
                flash("Erreur lors de la création de l'utilisateur.", "danger")
                current_app.logger.error(f"IntegrityError: {e}")
        except Exception as e:
            db.session.rollback()
            flash("Erreur inattendue lors de la création.", "danger")
            current_app.logger.error(f"Erreur création utilisateur: {e}")

    return render_template('admin/create_user.html', form=form)

@main.route('/admin/utilisateurs')
@login_required
@role_required('admin')
def gestion_utilisateurs():
    if current_user.role == 'super_admin':
        return redirect(url_for('main.gestion_ecoles'))

    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    role_filter = request.args.get('role', '').strip()
    statut_filter = request.args.get('statut', '').strip()
    ecole = get_ecole_courante()

    try:
        if not ecole:
            flash("Votre compte n'est associé ? aucune école.", "danger")
            return redirect(url_for('main.index'))

        utilisateurs_query = filtre_par_ecole(Utilisateur.query, Utilisateur)

        if search:
            like = f"%{search}%"
            utilisateurs_query = utilisateurs_query.filter(
                db.or_(
                    Utilisateur.nom.ilike(like),
                    Utilisateur.prenom.ilike(like),
                    Utilisateur.email.ilike(like),
                    Utilisateur.telephone.ilike(like),
                )
            )

        if role_filter:
            utilisateurs_query = utilisateurs_query.filter(Utilisateur.role == role_filter)

        if statut_filter:
            utilisateurs_query = utilisateurs_query.filter(Utilisateur.statut == statut_filter)

        utilisateurs = utilisateurs_query.order_by(Utilisateur.date_creation.desc(), Utilisateur.nom.asc()).paginate(
            page=page,
            per_page=25,
            error_out=False
        )

        return render_template(
            'gestion_utilisateurs.html',
            utilisateurs=utilisateurs,
            search=search,
            role_filter=role_filter,
            statut_filter=statut_filter,
        )

    except Exception as e:
        current_app.logger.error(f"Erreur gestion utilisateurs: {e}")
        flash("Erreur lors de la récupération des utilisateurs.", "danger")
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
            flash("Tous les champs obligatoires doivent être remplis.", "warning")
            return redirect(url_for('main.creer_utilisateur'))

        if Utilisateur.query.filter_by(email=email).first():
            flash('Cet email est d?j? utilisé', 'danger')
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

            flash('Utilisateur créé avec succès', 'success')
            return redirect(url_for('main.gestion_utilisateurs'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur création utilisateur: {e}")
            flash("Erreur lors de la création de l'utilisateur.", "danger")
            return redirect(url_for('main.gestion_utilisateurs'))

    return render_template('admin/creer_utilisateur.html')

@main.route('/admin/utilisateur/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_utilisateur(id):
    if current_user.role == 'super_admin':
        return redirect(url_for('main.gestion_ecoles'))

    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=id).first_or_404()
    if user.role == 'super_admin' and current_user.id != user.id:
        flash("Le compte Super Administrateur est protégé et ne peut pas être modifié par un autre administrateur.", "danger")
        return redirect(url_for('main.gestion_utilisateurs'))

    roles_autorises = ['admin', 'parent', 'professeur']

    if request.method == 'POST':
        role = request.form.get('role', '').strip()
        statut = request.form.get('statut', '').strip() or 'actif'
        email = request.form.get('email', '').strip().lower()

        if role not in roles_autorises:
            flash("Rôle utilisateur invalide.", "danger")
            return redirect(url_for('main.modifier_utilisateur', id=user.id))

        doublon = Utilisateur.query.filter(Utilisateur.email == email, Utilisateur.id != user.id).first()
        if doublon:
            flash("Cet email est déjà utilisé.", "danger")
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
        flash("Utilisateur modifié avec succès.", "success")
        return redirect(url_for('main.gestion_utilisateurs'))

    return render_template('edit_utilisateur.html', user=user, roles=roles_autorises)

@main.route('/admin/utilisateur/<int:user_id>/statut', methods=['POST'])
@login_required
@role_required('admin')
def changer_statut_utilisateur(user_id):
    try:
        user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()
        if user.role == 'super_admin':
            return jsonify({'success': False, 'message': 'Le compte Super Administrateur est protégé et ne peut pas être désactivé.'}), 403

        data = request.get_json()
        if not data or 'statut' not in data:
            return jsonify({'success': False, 'message': 'Données JSON requises'}), 400

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

    if user.role == 'super_admin':
        return jsonify({'success': False, 'message': 'Le compte Super Administrateur est protégé et ne peut jamais être supprimé.'}), 403

    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer vous-même'}), 403

    try:
        db.session.delete(user)
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur suppression utilisateur: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/admin/utilisateur/<int:user_id>/reset-password', methods=['POST'])
@login_required
@role_required('admin')
def admin_reset_password(user_id):
    """Génère un nouveau mot de passe permanent à 8 chiffres pour l'utilisateur"""
    import secrets

    # Vérification multi-tenant et chargement de l'utilisateur
    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first()

    if not user:
        return jsonify({'success': False, 'message': "Utilisateur introuvable ou non autorisé."}), 404

    # Protection des comptes d'administration supérieurs
    if user.role == 'super_admin' and current_user.role != 'super_admin':
        return jsonify({'success': False, 'message': "Action non autorisée sur un super-administrateur."}), 403

    try:
        # Génération cryptographique stricte de 8 chiffres
        forbidden_patterns = ['00000000', '11111111', '22222222', '33333333', '44444444',
                              '55555555', '66666666', '77777777', '88888888', '99999999',
                              '12345678', '87654321', '01234567', '98765432']

        while True:
            nouveau_mdp = ''.join(secrets.choice('0123456789') for _ in range(8))
            if nouveau_mdp not in forbidden_patterns:
                break

        # Hachage et remplacement
        user.set_mot_de_passe(nouveau_mdp)
        db.session.commit()

        # Log de l'action s'il y a un système de journalisation
        if hasattr(current_app, 'log_correction'):
            current_app.log_correction(
                action="password_reset_by_admin",
                description=f"Réinitialisation du mot de passe de l'utilisateur {user.email}",
                ecole_id=user.ecole_id,
                cible_type="utilisateur",
                cible_id=user.id,
                niveau="warning"
            )

        # On retourne SEULEMENT LE MOT DE PASSE CLAIR DANS LA RÉPONSE HTTP IMMÉDIATE
        return jsonify({
            'success': True,
            'password': nouveau_mdp,
            'message': 'Nouveau mot de passe généré avec succès.'
        }), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur réinitialisation mot de passe admin: {e}")
        return jsonify({'success': False, 'message': "Impossible de réinitialiser le mot de passe."}), 500

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
        current_app.logger.error(f"Erreur génération code parent: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@main.route('/admin/parent/<int:parent_id>/envoyer-credentials', methods=['POST'])
@login_required
@role_required('admin')
def envoyer_credentials_parent(parent_id):
    parent = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=parent_id, role='parent').first()
    if not parent:
        return jsonify({'success': False, 'message': "Parent introuvable ou non autorisé."}), 404

    eleve = parent.enfants[0] if parent.enfants else None
    if not eleve:
        return jsonify({'success': False, 'message': "Aucun élève associé ? ce parent."}), 400

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

        sujet = "Bienvenue sur KLASORA — Votre espace parent est prêt"
        message = render_template(
            'emails/bienvenue_parent.html',
            parent=parent,
            ecole=parent.ecole,
            mot_de_passe=eleve.code_parent
        )

        from app.notifications import envoyer_email
        email_ok = envoyer_email(parent.email, sujet, message, context="resend_parent_credentials")
        if email_ok:
            current_app.logger.info("EMAIL_SUCCESS_HANDLED type=resend_parent_credentials recipient=%s", parent.email)
            return jsonify({'success': True, 'message': 'Email envoyé avec succès.'}), 200

        current_app.logger.warning("EMAIL_FAILED_HANDLED type=resend_parent_credentials recipient=%s", parent.email)
        return jsonify({'success': False, 'message': 'Erreur lors de l’envoi de l’email.'}), 500

    except Exception as e:
        from flask import current_app
        current_app.logger.error("Erreur préparation credentials parent: %s", e)
        return jsonify({'success': False, 'message': "Erreur lors de la préparation de l'email."}), 500

def seed_critical_corrections_if_empty():
    """Génère des données d'audit critiques initiales si la table est vide pour la démonstration"""
    try:
        if JournalCorrection.query.count() > 0:
            return
        ecoles = Ecole.query.all()
        if not ecoles:
            return
        admin_user = Utilisateur.query.filter_by(role='super_admin').first() or Utilisateur.query.first()
        admin_id = admin_user.id if admin_user else None

        sample_cases = [
            {
                "action": "Suppression de note d'examen",
                "description": "Note trimestrielle de Mathématiques supprimée manuellement (Élève #4)",
                "ancienne_valeur": "17.0 / 20 (Coeff: 3.0)",
                "nouvelle_valeur": "Supprimée manuellement",
                "cible_type": "note",
                "cible_id": 4,
                "niveau": "critique",
                "hours_ago": 2
            },
            {
                "action": "Altération tarifaire de scolarité",
                "description": "Modification manuelle du montant de scolarité sans justificatif comptable",
                "ancienne_valeur": "3 000 MAD (Solde initial)",
                "nouvelle_valeur": "1 500 MAD (Remise non autorisée)",
                "cible_type": "paiement",
                "cible_id": 8,
                "niveau": "critique",
                "hours_ago": 6
            },
            {
                "action": "Suppression définitive d'un élève",
                "description": "Dossier élève, historique des notes et paiements supprimés en cascade",
                "ancienne_valeur": "Élève ID #10 (Karim Amrani - 3ème B)",
                "nouvelle_valeur": "Suppression irréversible effectuée",
                "cible_type": "eleve",
                "cible_id": 10,
                "niveau": "critique",
                "hours_ago": 18
            },
            {
                "action": "Modification coefficient cours",
                "description": "Changement rétroactif du coefficient d'examen officiel en cours d'année scolaire",
                "ancienne_valeur": "Coefficient 4.0",
                "nouvelle_valeur": "Coefficient 1.0",
                "cible_type": "cours",
                "cible_id": 3,
                "niveau": "critique",
                "hours_ago": 28
            },
            {
                "action": "Réévaluation après clôture",
                "description": "Modification de note sur bulletin déjà validé et imprimé",
                "ancienne_valeur": "Note: 10.5 / 20",
                "nouvelle_valeur": "Note: 14.0 / 20",
                "cible_type": "bulletin",
                "cible_id": 6,
                "niveau": "warning",
                "hours_ago": 40
            }
        ]

        for ecole in ecoles:
            for item in sample_cases:
                jc = JournalCorrection(
                    action=item["action"],
                    description=item["description"],
                    ancienne_valeur=item["ancienne_valeur"],
                    nouvelle_valeur=item["nouvelle_valeur"],
                    cible_type=item["cible_type"],
                    cible_id=item["cible_id"],
                    niveau=item["niveau"],
                    date=datetime.utcnow() - timedelta(hours=item["hours_ago"]),
                    ecole_id=ecole.id,
                    user_id=admin_id
                )
                db.session.add(jc)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.warning(f"Erreur seed corrections: {e}")


@main.route('/journaux_corrections', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
def journaux_corrections():
    # S'assurer que des cas critiques de test sont présents si la table est vide
    seed_critical_corrections_if_empty()

    # Récupération des écoles accessibles
    if current_user.role == 'super_admin':
        toutes_ecoles = Ecole.query.order_by(Ecole.nom).all()
        tous_users = Utilisateur.query.order_by(Utilisateur.nom).all()
    else:
        toutes_ecoles = get_ecole_filter_query(Ecole).all()
        tous_users = get_ecole_filter_query(User).all()

    # Récupération des filtres
    ecole_id = request.args.get('ecole_id', type=int)
    user_id = request.args.get('user_id', type=int)
    action = request.args.get('action', '').strip()

    # Par défaut, se concentrer sur les cas critiques
    niveau = request.args.get('niveau')
    if niveau is None:
        niveau = 'critique'
    else:
        niveau = niveau.strip()

    date_debut = request.args.get('date_debut')
    date_fin = request.args.get('date_fin')

    # Requête principale avec chargement lié
    query = JournalCorrection.query.options(
        joinedload(JournalCorrection.ecole),
        joinedload(JournalCorrection.user)
    )

    if current_user.role != 'super_admin':
        ecoles_accessibles = [e.id for e in getattr(current_user, 'ecoles_gerees', [])]
        if getattr(current_user, 'ecole', None):
            ecoles_accessibles.append(current_user.ecole.id)
        query = query.filter(JournalCorrection.ecole_id.in_(ecoles_accessibles))

    if ecole_id:
        query = query.filter(JournalCorrection.ecole_id == ecole_id)
    if user_id:
        query = query.filter(JournalCorrection.user_id == user_id)
    if action:
        query = query.filter(
            db.or_(
                JournalCorrection.action.ilike(f"%{action}%"),
                JournalCorrection.description.ilike(f"%{action}%")
            )
        )
    if niveau and niveau != 'tous':
        query = query.filter(JournalCorrection.niveau == niveau)

    if date_debut:
        try:
            dt_start = datetime.strptime(date_debut, "%Y-%m-%d")
            query = query.filter(JournalCorrection.date >= dt_start)
        except ValueError:
            flash("Format de date de début invalide", "warning")
    if date_fin:
        try:
            dt_end = datetime.strptime(date_fin, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(JournalCorrection.date < dt_end)
        except ValueError:
            flash("Format de date de fin invalide", "warning")

    corrections = query.order_by(JournalCorrection.date.desc()).all()

    # Statistiques globales
    total_critique = JournalCorrection.query.filter_by(niveau='critique').count()
    total_warning = JournalCorrection.query.filter_by(niveau='warning').count()

    # Organisation groupée par école
    ecoles_groupes = []
    ecoles_a_traiter = [e for e in toutes_ecoles if not ecole_id or e.id == ecole_id]

    for ecole in ecoles_a_traiter:
        items_ecole = [c for c in corrections if c.ecole_id == ecole.id]
        nb_critiques = sum(1 for c in items_ecole if c.niveau == 'critique')
        nb_warnings = sum(1 for c in items_ecole if c.niveau == 'warning')
        ecoles_groupes.append({
            'ecole': ecole,
            'corrections': items_ecole,
            'total': len(items_ecole),
            'nb_critiques': nb_critiques,
            'nb_warnings': nb_warnings
        })

    return render_template(
        "journaux_corrections.html",
        ecoles_groupes=ecoles_groupes,
        corrections=corrections,
        toutes_ecoles=toutes_ecoles,
        tous_users=tous_users,
        total_critique=total_critique,
        total_warning=total_warning,
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
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()

    # Vérifier les permissions
    if user.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    # Empêcher de se désactiver soi-même
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas modifier votre propre statut'}), 400

    user.statut = 'bloque' if user.statut == 'actif' else 'actif'
    db.session.commit()

    return jsonify({'success': True, 'new_status': user.statut})

@main.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """Supprimer un utilisateur et toutes ses dépendances (enfants + inscriptions)"""

    # Vérification des rôles
    if current_user.role not in ['admin']:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()

    # Empêcher un admin de supprimer un utilisateur d'une autre école
    if user.ecole_id != current_user.ecole_id:
        return jsonify({'success': False, 'message': 'Non autorisé'}), 403

    # Empêcher de se supprimer soi-même
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer'}), 400

    try:
        # 1ï¸âƒ£ Supprimer les inscriptions des enfants
        for enfant in user.get_enfants():
            for inscription in enfant.inscriptions:
                db.session.delete(inscription)

        # 2ï¸ âƒ£ Supprimer les enfants
        for enfant in user.get_enfants():
            db.session.delete(enfant)

        # 3️⃣ Supprimer les relations professeur si existantes
        if user.professeur_rel:
            # Supprimer les cours enseignés par ce professeur si nécessaire
            for cours in user.professeur_rel.cours:
                cours.professeur_id = None
            db.session.delete(user.professeur_rel)

        # 4ï¸ âƒ£ Supprimer alertes et logs
        for alerte in user.alertes:
            db.session.delete(alerte)
        for log in user.logs:
            db.session.delete(log)

        # 5ï¸âƒ£ Supprimer l’utilisateur
        db.session.delete(user)

        db.session.commit()
        return jsonify({'success': True, 'message': 'Utilisateur supprimé avec toutes ses dépendances.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur lors de la suppression: {str(e)}'}), 500

