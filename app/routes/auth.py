from . import main
from .common import (
    Classe,
    Cours,
    Ecole,
    Eleve,
    EmploiTemps,
    LoginForm,
    Paiement,
    Professeur,
    URLSafeTimedSerializer,
    Utilisateur,
    check_password_hash,
    current_app,
    current_user,
    datetime,
    db,
    escape,
    flash,
    generate_password_hash,
    get_ecole_filter_query,
    get_remote_address,
    limiter,
    login_required,
    login_user,
    logout_user,
    redirect,
    render_template,
    request,
    role_required,
    selectinload,
    session,
    url_for,
)


@main.route('/')
@login_required
def index():
    """Route principale - Redirige vers le dashboard approprié selon le rôle"""
    current_user.dernier_acces = datetime.utcnow()
    db.session.commit()

    # -----------------------------
    # ADMIN / SUPER_ADMIN
    # -----------------------------
    if current_user.role in ['admin', 'super_admin']:
        ecole_id = current_user.ecole_id  # ✅ Filtrage multi-écoles
        stats = {
            'total_eleves': Eleve.query.filter_by(ecole_id=ecole_id).count(),
            'total_professeurs': Professeur.query.filter_by(ecole_id=ecole_id).count(),
            'total_cours': Cours.query.filter_by(ecole_id=ecole_id).count(),
            'paiements_attente': Paiement.query.filter_by(ecole_id=ecole_id, statut='en attente').count(),
            'eleves_nouveaux': Eleve.query.filter(
                Eleve.ecole_id==ecole_id,
                Eleve.date_inscription >= datetime.utcnow().replace(day=1)
            ).count()
        }
        return render_template('index.html', stats=stats)

    # -----------------------------
    # ENSEIGNANT / PROFESSEUR
    # -----------------------------
    elif current_user.role in ['enseignant', 'professeur']:
        emploi_temps = EmploiTemps.query.filter_by(professeur_id=current_user.id).all()
        return render_template('enseignant_home.html', emploi_temps=emploi_temps)

    # -----------------------------
    # PARENT
    # -----------------------------
    elif current_user.role == 'parent':
        return render_template('parent_home.html')

    # -----------------------------
    # ROLE INCONNU
    # -----------------------------
    else:
        flash("Rôle inconnu. Veuillez contacter l'administrateur.", "warning")
        logout_user()
        return redirect(url_for('main.login'))

@main.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", key_func=get_remote_address)  # limite par IP
def login():
    """Route de connexion principale pour tous les utilisateurs avec sécurité multi-écoles"""
    if current_user.is_authenticated:
        role = getattr(current_user, "role", None)
        endpoint_par_role = {
            "admin": "main.admin_dashboard",
            "super_admin": "main.admin_dashboard",
            "enseignant": "main.enseignant_dashboard",
            "professeur": "main.enseignant_dashboard",
            "parent": "main.parent_dashboard",
        }
        return redirect(url_for(endpoint_par_role.get(role, "main.index")))

    form = LoginForm()
    if form.validate_on_submit():
        # sanitize + normaliser l'identifiant
        identifiant = escape(form.email.data.strip().lower())

        # Option: implementer un throttle/lockout par identifiant ici (compte)
        # Exemple (pseudo): if too_many_failed_attempts(identifiant): flash(...); return redirect(...)

        # Recherche utilisateur par email (case-insensitive) ou telephone
        # Assure-toi d'avoir les colonnes indexées pour la perf
        query = Utilisateur.query.filter(
            (Utilisateur.email.ilike(identifiant)) | (Utilisateur.telephone == identifiant)
        )
        utilisateur = query.first()

        # IP via get_remote_address (plus fiable avec flask-limiter)
        ip = get_remote_address()

        if utilisateur and check_password_hash(utilisateur.mot_de_passe, form.mot_de_passe.data):
            # utilisateur existe et mot de passe correct

            # Vérification école pour tous sauf super_admin
            if utilisateur.role != "super_admin" and not utilisateur.ecole_id:
                flash("Votre compte n'est associé à aucune école. Contactez l'administrateur.", "danger")
                current_app.logger.warning(f"Connexion échouée (pas d'école) pour {identifiant} depuis {ip}")
                return redirect(url_for("main.login"))

            # Nettoyage / mitigation session fixation
            session_keys = list(session.keys())
            for k in session_keys:
                session.pop(k, None)

            # Mapping rôles
            role_login = "enseignant" if utilisateur.role == "professeur" else utilisateur.role

            login_user(utilisateur)  # tu peux ajouter remember=form.remember.data si tu veux
            session["role"] = role_login

            # ASSIGNATION ÉCOLE
            if utilisateur.role == "admin" and utilisateur.ecole_id:
                session["ecole_id"] = utilisateur.ecole_id
                current_app.logger.info(f"École assignée automatiquement à {utilisateur.email} (admin) depuis {ip}")
            elif utilisateur.role == "super_admin":
                # get_ecole_filter_query doit être défini ailleurs : renvoie query Ecole (filtrée si nécessaire)
                premiere_ecole = get_ecole_filter_query(Ecole).first()
                if premiere_ecole:
                    session["ecole_id"] = premiere_ecole.id
                    current_app.logger.info(f"École par défaut assignée à super_admin {utilisateur.email} depuis {ip}")

            # Logging succinct (éviter d'écrire info sensibles)
            current_app.logger.info(f"Connexion réussie pour utilisateur id={utilisateur.id} depuis {ip} rôle={role_login}")

            # traitement safe du next param (ne pas rediriger vers un domaine externe)
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'):
                next_page = None

            endpoint_par_role = {
                "admin": "main.admin_dashboard",
                "super_admin": "main.admin_dashboard",
                "enseignant": "main.enseignant_dashboard",
                "parent": "main.parent_dashboard",
            }
            return redirect(next_page) if next_page else redirect(url_for(endpoint_par_role.get(role_login, "main.index")))
        else:
            # échec de connexion
            current_app.logger.warning(f"Tentative de connexion échouée pour identifiant={identifiant} depuis {ip}")
            flash('Identifiant ou mot de passe incorrect', 'danger')

    return render_template('login.html', form=form)

@main.route('/portal_parent')
@login_required
@role_required('parent')
def portal_parent():
    """Portail parent sécurisé multi-écoles avec vue consolidée des enfants"""
    try:
        # Vérifier que le parent a bien une ecole_id (si la logique le requiert)
        if not getattr(current_user, "ecole_id", None):
            flash("Votre compte parent n'est associé à aucune école.", "warning")
            return redirect(url_for('main.parent_dashboard'))

        # Requête filtrée strictement par parent ET par école (prévenir fuite de données)
        enfants = (
            db.session.query(Eleve)
            .filter(
                Eleve.parent_id == current_user.id,
                Eleve.ecole_id == current_user.ecole_id
            )
            .options(
                selectinload(Eleve.notes),
                selectinload(Eleve.absences),
                selectinload(Eleve.paiements)
            )
            .all()
        )

        if not enfants:
            flash("Aucun élève associé à votre compte parent dans votre école", "warning")
            return redirect(url_for('main.parent_dashboard'))

        # Calculs légers côté application : acceptable si le nombre d'enfants est limité
        for eleve in enfants:
            eleve.notes_sorted = sorted(eleve.notes, key=lambda n: n.date_evaluation or datetime.min, reverse=True)
            eleve.absences_sorted = sorted(eleve.absences, key=lambda a: a.date_absence or datetime.min, reverse=True)
            eleve.paiements_sorted = sorted(eleve.paiements, key=lambda p: p.date_paiement or datetime.min, reverse=True)

            total_pondere = sum((n.valeur or 0) * (n.coefficient or 0) for n in eleve.notes)
            total_coefficients = sum((n.coefficient or 0) for n in eleve.notes)
            eleve.moyenne = round(total_pondere / total_coefficients, 2) if total_coefficients else 0

            eleve.total_notes = len(eleve.notes)
            eleve.total_absences = len(eleve.absences)
            eleve.total_paiements = len(eleve.paiements)

        current_app.logger.info(f"Parent id={current_user.id} a accédé au portal_parent depuis {get_remote_address()}")

        return render_template('portal_parent.html', enfants=enfants)

    except Exception as e:
        current_app.logger.exception(f"Erreur portal_parent pour parent id={current_user.id}")
        flash("Une erreur est survenue. Veuillez réessayer plus tard.", "danger")
        return redirect(url_for('main.parent_dashboard'))

@main.route('/logout')
@login_required
def logout():
    """Déconnexion générale sécurisée de l'application"""

    current_app.logger.info(f"Déconnexion de l'utilisateur id={current_user.id} - rôle={current_user.role}")

    # Clear session puis logout
    session_keys = list(session.keys())
    for k in session_keys:
        session.pop(k, None)

    logout_user()

    # Optionnel: force session cookie nouvelle génération côté client (si utilisé)
    # session.modified = True

    flash('Vous avez été déconnecté avec succès', 'info')
    return redirect(url_for('main.login'))

@main.route('/aide')
@login_required
def aide():
    """Page d'aide et support du site"""
    return render_template('aide.html')

@main.route('/inscription_parent', methods=['GET', 'POST'])
def inscription_parent():
    # Récupérer toutes les classes disponibles pour le select
    classes = get_ecole_filter_query(Classe).all()

    if request.method == 'POST':
        nom_enfant = request.form.get('nom_enfant')
        prenom_enfant = request.form.get('prenom_enfant')
        date_naissance_str = request.form.get('date_naissance')
        classe_id = request.form.get('classe')  # on récupère l'id de la classe
        nom_parent = request.form.get('nom_parent')
        prenom_parent = request.form.get('prenom_parent')
        email_parent = request.form.get('email')
        telephone_parent = request.form.get('telephone_parent')

        if not all([nom_enfant, prenom_enfant, date_naissance_str, classe_id, nom_parent, prenom_parent, email_parent]):
            flash("Tous les champs sont obligatoires.", "warning")
            return redirect(url_for('main.inscription_parent'))

        # Conversion date de naissance
        try:
            date_naissance = datetime.strptime(date_naissance_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Format de date invalide.", "danger")
            return redirect(url_for('main.inscription_parent'))

        # Récupérer l'objet Classe
        classe_obj = Classe.query.get(classe_id)
        if not classe_obj:
            flash("Classe invalide.", "danger")
            return redirect(url_for('main.inscription_parent'))

        # Génération du code parent unique
        code_parent = Eleve.generer_code_parent()

        # Création de l'élève
        nouvel_eleve = Eleve(
            nom=nom_enfant,
            prenom=prenom_enfant,
            date_naissance=date_naissance,
            classe=classe_obj,       # association avec l'objet Classe
            code_parent=code_parent,
            email_parent=email_parent,
            telephone_parent=telephone_parent
        )
        db.session.add(nouvel_eleve)
        db.session.commit()

        flash("Inscription réussie !", "success")
        return redirect(url_for('main.inscription_parent'))

    return render_template('inscription_parent.html', classes=classes)

@main.route('/request_reset_password', methods=['GET', 'POST'])
def request_reset_password():
    """Page pour demander un lien de réinitialisation par email"""
    from app.forms import RequestResetPasswordForm
    from app.notifications import envoyer_email  # <-- Import correct, même que pour ajouter_eleve

    form = RequestResetPasswordForm()

    if form.validate_on_submit():
        email = form.email.data.lower()
        utilisateur = Utilisateur.query.filter_by(email=email).first()

        if utilisateur:
            # Génération du token sécurisé
            serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
            token = serializer.dumps(email, salt=current_app.config['SECURITY_PASSWORD_SALT'])

            reset_link = url_for('main.reset_password_token', token=token, _external=True)
            sujet = "Réinitialisation de votre mot de passe"
            
            # Message HTML compatible Gmail
            message = f"""
            <html>
            <body style="font-family:Arial,sans-serif; background:#f4f4f4; padding:20px;">
                <div style="max-width:600px; margin:auto; background:#fff; border-radius:10px; padding:20px; box-shadow:0 0 10px rgba(0,0,0,0.1);">
                    <h2 style="color:#4CAF50;">Bonjour {utilisateur.nom},</h2>
                    <p>Pour réinitialiser votre mot de passe, cliquez sur le lien suivant :</p>
                    <p><a href="{reset_link}" style="display:inline-block; padding:10px 20px; background:#4CAF50; color:#fff; text-decoration:none; border-radius:5px;">Réinitialiser mon mot de passe</a></p>
                    <p>Ce lien est valable 1 heure.</p>
                    <p>Si vous n'avez pas demandé cette réinitialisation, ignorez ce message.</p>
                    <p style="font-size:12px; color:#555;">Cordialement,<br>L'équipe EduManage</p>
                </div>
            </body>
            </html>
            """

            try:
                # Utilisation de la fonction centralisée comme pour ajouter_eleve
                envoyer_email(utilisateur.email, sujet, message)
                current_app.logger.info(f"Email de reset envoyé à {utilisateur.email}")
            except Exception as e:
                current_app.logger.error(f"Erreur envoi email reset: {e}")

        else:
            current_app.logger.info(f"Tentative de reset pour email inexistant: {email}")

        # Message générique pour éviter de révéler l'existence d'un compte
        flash("Si un compte existe pour cet email, un lien de réinitialisation a été envoyé.", "info")
        return redirect(url_for('main.login'))

    return render_template('request_reset_password.html', form=form)

@main.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password_token(token):
    """Réinitialisation du mot de passe via token sécurisé"""
    from app.forms import ResetPasswordConfirmForm

    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(
            token, 
            salt=current_app.config['SECURITY_PASSWORD_SALT'], 
            max_age=3600  # lien valable 1 heure
        )
    except Exception:
        flash("Le lien de réinitialisation est invalide ou expiré.", "danger")
        return redirect(url_for('main.login'))

    form = ResetPasswordConfirmForm()

    if form.validate_on_submit():
        new_password = form.new_password.data
        utilisateur = Utilisateur.query.filter_by(email=email).first()

        if utilisateur:
            try:
                utilisateur.mot_de_passe = generate_password_hash(new_password)
                db.session.commit()
                flash("Mot de passe réinitialisé avec succès ! Vous pouvez maintenant vous connecter.", "success")
                return redirect(url_for('main.login'))
            except Exception as e:
                current_app.logger.error(f"Erreur mise à jour mot de passe: {e}")
                flash("Erreur lors de la réinitialisation. Veuillez réessayer.", "danger")
                return redirect(url_for('main.login'))

        flash("Utilisateur introuvable.", "danger")
        return redirect(url_for('main.login'))

    return render_template('reset_password.html', form=form, token=token)
