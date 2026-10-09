from . import main
from .common import (
    Ecole,
    Eleve,
    IntegrityError,
    JournalCorrection,
    Utilisateur,
    current_app,
    current_user,
    datetime,
    db,
    filtre_par_ecole,
    flash,
    get_ecole_courante,
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
from app.access_codes import generate_access_code
from app.services.phone_numbers import normaliser_numero_whatsapp
from app.services.whatsapp_queue import enqueue_message


def _notifier_whatsapp_reset_password(user, nouveau_mdp):
    try:
        if not user or not getattr(user, "ecole_id", None):
            return None

        ecole = getattr(user, "ecole", None) or db.session.get(Ecole, user.ecole_id)
        if not ecole or not getattr(ecole, "whatsapp_enabled", False):
            return None

        telephone = normaliser_numero_whatsapp(getattr(user, "telephone", None))
        if not telephone:
            current_app.logger.warning(
                "Notification WhatsApp reset password ignoree: telephone absent user_id=%s",
                getattr(user, "id", None),
            )
            return None

        nom = f"{user.prenom or ''} {user.nom or ''}".strip() or "utilisateur"
        message = (
            f"Bonjour {nom}, votre mot de passe KLASORA a ete mis a jour par l'administration "
            f"de {ecole.nom}. Acces : https://klasora.com - Numero : {telephone}. "
            f"Mot de passe : {nouveau_mdp}."
        )
        return enqueue_message(
            ecole_id=ecole.id,
            destinataire=telephone,
            message=message,
            type_message="general",
            commit=True,
        )
    except Exception as exc:
        current_app.logger.warning(
            "Notification WhatsApp reset password ignoree user_id=%s: %s",
            getattr(user, "id", None),
            exc,
        )
        return None


def _parent_delete_block_response(user):
    enfants_count = len(user.get_enfants()) if user.role == 'parent' else 0
    if user.role != 'parent' or enfants_count == 0:
        return None

    message = (
        f"Ce compte parent est rattaché à {enfants_count} élève(s). "
        "Vous ne pouvez pas le supprimer. Pour changer de responsable, "
        "modifiez directement ses informations (nom, téléphone, email) "
        "sur sa fiche ou réassignez l'élève."
    )
    return jsonify({'success': False, 'message': message}), 400


def _primary_admin_for_ecole(ecole_id):
    if not ecole_id:
        return None
    return (
        Utilisateur.query
        .filter(Utilisateur.ecole_id == ecole_id, Utilisateur.role.in_(('admin', 'administrateur')))
        .order_by(Utilisateur.date_creation.asc(), Utilisateur.id.asc())
        .first()
    )


def _is_primary_admin(user):
    primary_admin = _primary_admin_for_ecole(user.ecole_id)
    return bool(primary_admin and primary_admin.id == user.id)


def _refuser_mutation_compte(action, user=None):
    current_app.logger.warning(
        "Tentative de mutation de compte non autorisee: acteur_id=%s cible_id=%s action=%s",
        current_user.id, getattr(user, 'id', None), action,
    )
    return jsonify({'success': False, 'message': 'Action non autorisée sur ce compte.'}), 403


def _garde_mutation_compte(user, action):
    """Impose la même hiérarchie et le même périmètre à toutes les mutations."""
    ecole = get_ecole_courante()
    if not ecole or isinstance(ecole, tuple) or user.ecole_id != ecole.id:
        return _refuser_mutation_compte(action, user)

    roles_super_admin = ('super_admin', 'super-admin', 'superadmin')
    acteur_super_admin = current_user.role in roles_super_admin
    if user.role in roles_super_admin:
        if not acteur_super_admin or user.id != current_user.id or action in ('suppression', 'statut'):
            return _refuser_mutation_compte(action, user)
    elif not acteur_super_admin:
        if current_user.role not in ('admin', 'administrateur'):
            return _refuser_mutation_compte(action, user)
        if user.role in ('admin', 'administrateur') and not _is_primary_admin(current_user):
            if user.id != current_user.id or action not in ('profil', 'mot_de_passe'):
                return _refuser_mutation_compte(action, user)
    return None


def _garde_creation_admin():
    ecole = get_ecole_courante()
    if not ecole or isinstance(ecole, tuple) or ecole.id != current_user.ecole_id or not _is_primary_admin(current_user):
        return _refuser_mutation_compte('creation_admin')
    return None


@main.route('/admin/create_user', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def create_user():
    """Compatibilité de l'ancienne URL avec la création canonique sécurisée."""
    refus = _garde_creation_admin()
    if refus:
        return refus
    if request.method == 'POST':
        return creer_utilisateur()
    return redirect(url_for('main.creer_utilisateur'))

@main.route('/admin/utilisateurs')
@login_required
@role_required('admin')
def gestion_utilisateurs():
    if current_user.role == 'super_admin':
        return redirect(url_for('main.gestion_ecoles'))

    search = request.args.get('search', '').strip()
    role_filter = request.args.get('role', '').strip()
    statut_filter = request.args.get('statut', '').strip()
    ecole = get_ecole_courante()

    try:
        if not ecole:
            flash("Votre compte n'est associé à aucune école.", "danger")
            return redirect(url_for('main.index'))

        primary_admin = _primary_admin_for_ecole(current_user.ecole_id)
        is_primary_admin = _is_primary_admin(current_user)
        utilisateurs_query = filtre_par_ecole(Utilisateur.query, Utilisateur)
        if primary_admin and not is_primary_admin:
            utilisateurs_query = utilisateurs_query.filter(Utilisateur.id != primary_admin.id)

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

        all_users = utilisateurs_query.order_by(Utilisateur.date_creation.desc(), Utilisateur.nom.asc()).all()

        grouped = {'admin': [], 'professeur': [], 'parent': []}
        for u in all_users:
            key = u.role if u.role in grouped else 'admin'
            grouped[key].append(u)

        return render_template(
            'gestion_utilisateurs.html',
            grouped=grouped,
            total_users=len(all_users),
            search=search,
            role_filter=role_filter,
            statut_filter=statut_filter,
            primary_admin_id=primary_admin.id if primary_admin else None,
            can_create_admin=is_primary_admin,
        )

    except Exception as e:
        current_app.logger.error(f"Erreur gestion utilisateurs: {e}")
        flash("Erreur lors de la récupération des utilisateurs.", "danger")
        return redirect(url_for('main.index'))

@main.route('/admin/creer_utilisateur', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def creer_utilisateur():
    refus = _garde_creation_admin()
    if refus:
        return refus

    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        email = request.form.get('email', '').strip().lower()
        telephone = request.form.get('telephone', '').strip() or None
        role = 'admin'
        mot_de_passe = request.form.get('mot_de_passe', request.form.get('password', '')).strip()

        if not all([nom, email]):
            flash("Tous les champs obligatoires doivent être remplis.", "warning")
            return redirect(url_for('main.creer_utilisateur'))

        mot_de_passe_genere = not mot_de_passe
        if mot_de_passe_genere:
            mot_de_passe = generate_access_code()

        if Utilisateur.query.filter(db.func.lower(Utilisateur.email) == email).first():
            flash('Cet email est déjà utilisé', 'danger')
            return redirect(url_for('main.gestion_utilisateurs'))

        try:
            nouvel_utilisateur = Utilisateur(
                nom=nom,
                prenom='',
                email=email,
                telephone=telephone,
                role=role,
                ecole_id=current_user.ecole_id,
                statut='actif'
            )
            nouvel_utilisateur.set_mot_de_passe(mot_de_passe)

            db.session.add(nouvel_utilisateur)
            db.session.commit()

            if mot_de_passe_genere:
                flash(f"Administrateur créé avec succès. Code d'accès généré : {mot_de_passe}", 'success')
            else:
                flash('Administrateur créé avec succès', 'success')
            return redirect(url_for('main.gestion_utilisateurs'))
        except IntegrityError:
            db.session.rollback()
            flash("Cet email ou ce téléphone est déjà utilisé.", "danger")
            return redirect(url_for('main.gestion_utilisateurs'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur création utilisateur: {e}")
            flash("Erreur lors de la création de l'utilisateur.", "danger")
            return redirect(url_for('main.gestion_utilisateurs'))

    return render_template('admin/creer_utilisateur.html')

@main.route('/admin/utilisateur/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def modifier_utilisateur(id):
    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=id).first_or_404()
    refus = _garde_mutation_compte(user, 'profil')
    if refus:
        return refus

    if request.method == 'POST':
        statut = request.form.get('statut')
        if statut is None:
            statut = user.statut
        else:
            statut = statut.strip()
            if statut not in ('actif', 'bloque'):
                return jsonify({'success': False, 'message': 'Statut invalide'}), 400
        if statut != user.statut:
            refus = _garde_mutation_compte(user, 'statut')
            if refus:
                return refus
        email = request.form.get('email', '').strip().lower()

        doublon = Utilisateur.query.filter(db.func.lower(Utilisateur.email) == email, Utilisateur.id != user.id).first()
        if doublon:
            flash("Cet email est déjà utilisé.", "danger")
            return redirect(url_for('main.modifier_utilisateur', id=user.id))

        user.nom = request.form.get('nom', user.nom).strip()
        user.prenom = request.form.get('prenom', user.prenom).strip()
        user.email = email
        user.telephone = request.form.get('telephone', '').strip() or None
        user.statut = statut

        db.session.commit()
        flash("Utilisateur modifié avec succès.", "success")
        if current_user.role == 'super_admin':
            return redirect(url_for('main.gestion_ecoles'))
        return redirect(url_for('main.gestion_utilisateurs'))

    return render_template(
        'edit_utilisateur.html',
        user=user,
        is_primary_admin=_is_primary_admin(user),
        parent_enfants=user.get_enfants() if user.role == 'parent' else [],
    )

@main.route('/admin/utilisateur/<int:user_id>/statut', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def changer_statut_utilisateur(user_id):
    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()
    refus = _garde_mutation_compte(user, 'statut')
    if refus:
        return refus
    try:
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
@role_required('admin', 'super_admin')
def supprimer_utilisateur(user_id):
    user = Utilisateur.query.filter_by(id=user_id).first_or_404()
    refus = _garde_mutation_compte(user, 'suppression')
    if refus:
        return refus

    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer vous-même'}), 403

    if _is_primary_admin(user):
        return jsonify({'success': False, 'message': "L'administrateur principal ne peut jamais être supprimé."}), 403

    blocked_response = _parent_delete_block_response(user)
    if blocked_response:
        return blocked_response

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
@role_required('admin', 'super_admin')
def admin_reset_password(user_id):
    """Génère un nouveau mot de passe permanent à 8 chiffres pour l'utilisateur"""
    # Vérification multi-tenant et chargement de l'utilisateur
    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first()

    if not user:
        return jsonify({'success': False, 'message': "Utilisateur introuvable ou non autorisé."}), 404

    refus = _garde_mutation_compte(user, 'mot_de_passe')
    if refus:
        return refus

    try:
        nouveau_mdp = generate_access_code()

        # Hachage et remplacement
        user.set_mot_de_passe(nouveau_mdp)
        db.session.commit()
        whatsapp_item = _notifier_whatsapp_reset_password(user, nouveau_mdp)

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
            'whatsapp_queued': bool(whatsapp_item),
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

    return jsonify({
        'success': True,
        'message': 'Code parent disponible. Communiquez-le via le coupon imprime ou WhatsApp.',
        'code_parent': eleve.code_parent,
        'telephone': parent.telephone,
    }), 200



@main.route('/journaux_corrections', methods=['GET'])
@login_required
@role_required('admin', 'super_admin')
def journaux_corrections():
    # Récupération des écoles accessibles
    ecoles_accessibles = None
    if current_user.role in ('super_admin', 'super-admin', 'superadmin'):
        toutes_ecoles = Ecole.query.order_by(Ecole.nom).all()
        tous_users = Utilisateur.query.order_by(Utilisateur.nom).all()
    else:
        ecoles_accessibles = {e.id for e in getattr(current_user, 'ecoles_gerees', [])}
        if current_user.ecole_id:
            ecoles_accessibles.add(current_user.ecole_id)
        toutes_ecoles = Ecole.query.filter(Ecole.id.in_(ecoles_accessibles)).order_by(Ecole.nom).all()
        tous_users = Utilisateur.query.filter(Utilisateur.ecole_id.in_(ecoles_accessibles)).order_by(Utilisateur.nom).all()

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

    if ecoles_accessibles is not None:
        query = query.filter(JournalCorrection.ecole_id.in_(ecoles_accessibles))

    statistiques_query = query

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

    # Statistiques du même périmètre d'accès que les événements affichés.
    total_critique = statistiques_query.filter_by(niveau='critique').count()
    total_warning = statistiques_query.filter_by(niveau='warning').count()

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
@role_required('admin', 'super_admin')
def toggle_user_status(user_id):
    """Changer le statut d'un utilisateur"""
    user = filtre_par_ecole(Utilisateur.query, Utilisateur).filter_by(id=user_id).first_or_404()

    refus = _garde_mutation_compte(user, 'statut')
    if refus:
        return refus

    # Empêcher de se désactiver soi-même
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas modifier votre propre statut'}), 400

    user.statut = 'bloque' if user.statut == 'actif' else 'actif'
    db.session.commit()

    return jsonify({'success': True, 'new_status': user.statut})

@main.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@role_required('admin', 'super_admin')
def delete_user(user_id):
    """Supprimer un utilisateur et toutes ses dépendances (enfants + inscriptions)"""
    user = Utilisateur.query.filter_by(id=user_id).first_or_404()

    refus = _garde_mutation_compte(user, 'suppression')
    if refus:
        return refus

    # Empêcher de se supprimer soi-même
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas vous supprimer'}), 400

    if _is_primary_admin(user):
        return jsonify({'success': False, 'message': "L'administrateur principal ne peut jamais être supprimé."}), 403

    blocked_response = _parent_delete_block_response(user)
    if blocked_response:
        return blocked_response

    try:
        # Supprimer les relations professeur si existantes
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
        return jsonify({'success': True, 'message': 'Utilisateur supprime.'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur lors de la suppression: {str(e)}'}), 500

