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
    # SUPER_ADMIN / ADMIN
    # -----------------------------
    if current_user.role == 'super_admin':
        try:
            from app.admin.scripts import get_system_stats
            sys_stats = get_system_stats()
        except Exception:
            sys_stats = {}

        stats = {
            'total_ecoles': Ecole.query.count(),
            'disk_usage': sys_stats.get('disk_usage', 'N/A'),
            'db_backend': sys_stats.get('db_backend', 'Base'),
            'db_version': sys_stats.get('db_version', 'N/A'),
            'last_backup': sys_stats.get('last_backup'),
            'table_count': sys_stats.get('table_count', 'N/A'),
        }
        return render_template('index.html', stats=stats)

    elif current_user.role == 'admin':
        from app.utils import get_school_setup_state
        from app.services.annees_scolaires import get_annee_consultee
        from app.services.statistiques_annuelles import get_dashboard_admin_annuel
        setup_state = get_school_setup_state(current_user.ecole_id)
        if not setup_state.get('setup_complete', False):
            return redirect(url_for('main.onboarding'))

        ecole_id = current_user.ecole_id  # ✅ Filtrage multi-écoles
        annee_consultee = get_annee_consultee(ecole_id)
        stats = get_dashboard_admin_annuel(ecole_id, annee_consultee)
        return render_template(
            'index.html',
            stats=stats,
            school_setup_state=setup_state,
            annee_consultee=annee_consultee
        )

    # -----------------------------
    # PROFESSEUR
    # -----------------------------
    elif current_user.role == 'professeur':
        return redirect(url_for('main.professeur_dashboard'))

    # -----------------------------
    # PARENT
    # -----------------------------
    elif current_user.role == 'parent':
        return redirect(url_for('main.parent_dashboard'))

    # -----------------------------
    # ROLE INCONNU
    # -----------------------------
    else:
        flash("Rôle inconnu. Veuillez contacter l'administrateur.", "warning")
        logout_user()
        return redirect(url_for('main.login'))

@main.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 50 per hour", key_func=get_remote_address)  # limite par IP
def login():
    """Route de connexion principale pour tous les utilisateurs avec sécurité multi-écoles"""
    if current_user.is_authenticated:
        role = getattr(current_user, "role", None)
        endpoint_par_role = {
            "admin": "main.index",
            "super_admin": "main.index",
                        "professeur": "main.professeur_dashboard",
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

            # Vérification école bloquée / suspendue
            if utilisateur.role != "super_admin" and utilisateur.ecole and utilisateur.ecole.statut in ('bloque', 'suspendu'):
                ecole = utilisateur.ecole
                if utilisateur.role == 'admin':
                    motif = f" Motif : {ecole.motif_blocage}." if ecole.motif_blocage else ""
                    flash(f"L'accès à votre établissement ({ecole.nom}) est suspendu.{motif} Veuillez contacter l'administration de la plateforme.", "danger")
                else:
                    # Confidentialité : les parents et professeurs ne voient JAMAIS le motif financier/administratif
                    flash(f"L'accès à l'espace de votre établissement ({ecole.nom}) est temporairement indisponible. Veuillez contacter la direction de votre école.", "danger")
                current_app.logger.warning(f"Connexion refusée (école bloquée id={ecole.id}) pour {identifiant} rôle={utilisateur.role} depuis {ip}")
                return redirect(url_for("main.login"))

            # Nettoyage / mitigation session fixation
            session_keys = list(session.keys())
            for k in session_keys:
                session.pop(k, None)

            login_user(utilisateur)
            session["role"] = utilisateur.role

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
            current_app.logger.info(f"Connexion réussie pour utilisateur id={utilisateur.id} depuis {ip} rôle={utilisateur.role}")

            # traitement safe du next param (ne pas rediriger vers un domaine externe)
            next_page = request.args.get('next')
            if next_page and not next_page.startswith('/'):
                next_page = None

            endpoint_par_role = {
                "admin": "main.index",
                "super_admin": "main.index",
                "professeur": "main.professeur_dashboard",
                "parent": "main.parent_dashboard",
            }
            return redirect(next_page) if next_page else redirect(url_for(endpoint_par_role.get(utilisateur.role, "main.index")))
        else:
            # échec de connexion
            current_app.logger.warning(f"Tentative de connexion échouée pour identifiant={identifiant} depuis {ip}")
            flash('Identifiant ou mot de passe incorrect', 'danger')

    return render_template('login.html', form=form)

@main.route('/portal_parent')
@login_required
@role_required('parent')
def portal_parent():
    """Route obsolète, redirige vers parent_dashboard"""
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
    response = redirect(url_for('main.login'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@main.route('/aide')
@login_required
def aide():
    """Page d'aide et support du site"""
    return render_template('aide.html')

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
                    <p style="font-size:12px; color:#555;">Cordialement,<br>L'équipe KLASORA</p>
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
