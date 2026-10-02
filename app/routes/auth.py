from . import main
from .common import (
    AnneeScolaire,
    Ecole,
    Eleve,
    LoginForm,
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
    session,
    url_for,
)
from flask import jsonify
from app.models import (
    Absence,
    Classe,
    Cours,
    DemandePresentation,
    Inscription,
    Log,
    Paiement,
    Professeur,
    SupportTicket,
)
from app.utils import sanitize_internal_url
from app.services.phone_numbers import cles_telephone_equivalentes, normaliser_telephone_international


def normaliser_telephone_niger(value):
    """Compat: normalise aussi les numeros internationaux parents."""
    return normaliser_telephone_international(value)


def _telephone_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _telephone_match_values(phone_keys):
    values = set(phone_keys)
    digit_values = set()
    for key in phone_keys:
        digits = _telephone_digits(key)
        if not digits:
            continue
        digit_values.add(digits)
        if len(digits) == 8:
            digit_values.add(f"227{digits}")
        elif digits.startswith("227") and len(digits) == 11:
            digit_values.add(digits[3:])
    return values, digit_values


def _telephone_digits_expr(column):
    expr = column
    for old in (" ", "-", ".", "(", ")", "+"):
        expr = db.func.replace(expr, old, "")
    return expr


def _telephone_match_filter(column, phone_keys):
    exact_values, digit_values = _telephone_match_values(phone_keys)
    filters = []
    if exact_values:
        filters.append(column.in_(sorted(exact_values)))
    if digit_values:
        filters.append(_telephone_digits_expr(column).in_(sorted(digit_values)))
    return db.or_(*filters) if filters else db.false()


def _resolve_utilisateur_par_telephone(phone):
    searched_keys = cles_telephone_equivalentes(phone)
    if not searched_keys:
        return None

    phone_keys = sorted(searched_keys)
    user_order = (
        db.case((Utilisateur.statut == "actif", 0), else_=1),
        Utilisateur.id.asc(),
    )

    utilisateur = (
        Utilisateur.query
        .filter(_telephone_match_filter(Utilisateur.telephone, phone_keys))
        .order_by(*user_order)
        .first()
    )
    if utilisateur:
        return utilisateur

    return (
        Utilisateur.query
        .join(Eleve, Eleve.parent_id == Utilisateur.id)
        .filter(
            Utilisateur.role == "parent",
            _telephone_match_filter(Eleve.contact_parent, phone_keys),
        )
        .order_by(*user_order)
        .first()
    )


@main.route('/')
def index():
    """Route principale - Vitrine publique pour visiteurs non connectés, ou tableau de bord pour utilisateurs connectés"""
    if not current_user.is_authenticated:
        session['visited_public_page'] = True
        return render_template('landing.html')

    # -----------------------------
    # SUPER_ADMIN / ADMIN
    # -----------------------------
    if current_user.role == 'super_admin':
        try:
            from app.admin.scripts import get_system_stats, get_maintenance_status
            sys_stats = get_system_stats()
            maint_status = get_maintenance_status()
        except Exception:
            sys_stats = {}
            maint_status = {'active': False}

        from datetime import timedelta
        active_24h_cutoff = datetime.utcnow() - timedelta(hours=24)
        active_24h_count = (
            Utilisateur.query
            .filter(
                Utilisateur.statut == 'actif',
                Utilisateur.dernier_acces.isnot(None),
                Utilisateur.dernier_acces >= active_24h_cutoff,
            )
            .count()
        )

        from app.services.school_lifecycle import is_school_disabled

        ecoles = Ecole.query.order_by(Ecole.id.desc()).all()
        total_eleves = 0
        for ecole in ecoles:
            ecole.nb_eleves = Eleve.query.filter_by(ecole_id=ecole.id).count()
            ecole.nb_classes = Classe.query.filter_by(ecole_id=ecole.id).count()
            ecole.nb_profs = Professeur.query.filter_by(ecole_id=ecole.id).count()
            total_eleves += ecole.nb_eleves
            admin_user = Utilisateur.query.filter_by(ecole_id=ecole.id, role='admin').first()
            ecole.admin_user = admin_user
            if not ecole.email and admin_user and admin_user.email:
                ecole.email = admin_user.email

        ecoles_actives_count = sum(1 for e in ecoles if e.statut in ('actif', 'active') and not is_school_disabled(e))
        ecoles_bloquees_count = sum(1 for e in ecoles if is_school_disabled(e) or e.statut in ('bloque', 'suspendu', 'inactive'))

        # Demandes de présentation / Démo récentes
        demandes_recentes = DemandePresentation.query.order_by(DemandePresentation.created_at.desc()).limit(5).all() if DemandePresentation else []
        nouvelles_demandes_count = DemandePresentation.query.filter_by(statut='nouvelle').count() if DemandePresentation else 0

        # Derniers journaux système
        derniers_logs = Log.query.order_by(Log.timestamp.desc()).limit(5).all() if Log else []

        # Tickets de support en attente
        tickets_ouverts = SupportTicket.query.filter(SupportTicket.statut.in_(['nouveau', 'en_cours'])).order_by(SupportTicket.created_at.desc()).limit(5).all() if SupportTicket else []
        nouveau_tickets_count = SupportTicket.query.filter_by(statut='nouveau').count() if SupportTicket else 0

        stats = {
            'total_ecoles': len(ecoles),
            'ecoles_actives': ecoles_actives_count,
            'ecoles_bloquees': ecoles_bloquees_count,
            'total_eleves': total_eleves,
            'active_24h': active_24h_count,
            'disk_usage': sys_stats.get('disk_usage', 'N/A'),
            'db_backend': sys_stats.get('db_backend', 'Base'),
            'db_version': sys_stats.get('db_version', 'N/A'),
            'last_backup': sys_stats.get('last_backup'),
            'table_count': sys_stats.get('table_count', 'N/A'),
        }

        mdp_auto = session.pop('_mdp_auto_ecole', None)
        mdp_auto_email = session.pop('_mdp_auto_email', None)
        mdp_auto_nom = session.pop('_mdp_auto_nom', None)

        return render_template(
            'index.html',
            stats=stats,
            ecoles=ecoles,
            maint_status=maint_status,
            demandes_recentes=demandes_recentes,
            nouvelles_demandes_count=nouvelles_demandes_count,
            derniers_logs=derniers_logs,
            tickets_ouverts=tickets_ouverts,
            nouveau_tickets_count=nouveau_tickets_count,
            mdp_auto=mdp_auto,
            mdp_auto_email=mdp_auto_email,
            mdp_auto_nom=mdp_auto_nom,
            annee_planifiee=None,
            etat_planifiee=None
        )

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
        force_tour_prompt = bool(session.pop('onboarding_just_completed', False))

        annee_planifiee = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut='planifiee').order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc()).first()
        etat_planifiee = None
        if annee_planifiee:
            from app.services.preparation_annee import get_etat_preparation_annee
            etat_planifiee = get_etat_preparation_annee(ecole_id, annee_planifiee.id)

        from app.services.google_mail import get_school_mail_status
        mail_status = get_school_mail_status(ecole_id)
        email_non_connecte = not mail_status.get('is_connected', False)

        # Détection d'alerte : Période clôturée avec période suivante non encore activée
        rappel_periode_suivante = None
        if annee_consultee:
            from app.models import PeriodeBulletin
            from app.routes.bulletins import _trier_periodes_chronologique
            periodes_annee = PeriodeBulletin.query.filter_by(
                ecole_id=ecole_id,
                annee_id=annee_consultee.id
            ).all()
            periodes_triees = _trier_periodes_chronologique(periodes_annee)
            for i, p in enumerate(periodes_triees):
                if p.publie and i + 1 < len(periodes_triees):
                    p_suiv = periodes_triees[i + 1]
                    if not p_suiv.periode_active:
                        rappel_periode_suivante = {
                            'periode_close': p,
                            'periode_a_activer': p_suiv
                        }
                        break

        # Données et formulaires pour les actions rapides et la vue d'accueil admin
        from datetime import date
        from sqlalchemy.orm import joinedload
        from app.forms import EleveForm, PaiementForm, AjouterEmploiForm
        from app.services.classes_annuelles import get_classes_ouvertes_annee
        from app.utils_classes import classes_triees_pedagogique

        # 1. Classes & Formulaire d'inscription élève
        eleve_form = EleveForm()
        if annee_consultee:
            classes_query_form = get_classes_ouvertes_annee(ecole_id, annee_consultee.id)
        else:
            classes_query_form = Classe.query.filter_by(ecole_id=ecole_id).filter(db.false())
        classes = classes_triees_pedagogique(classes_query_form).all()
        eleve_form.classe_id.choices = [(c.id, c.nom_complet if hasattr(c, 'nom_complet') else c.nom) for c in classes]
        eleve_form.parent_id.choices = [(0, "--- Aucun parent / Nouveau tuteur ---")]
        parents_form = Utilisateur.query.filter_by(role='parent', ecole_id=ecole_id).order_by(Utilisateur.nom).all()
        eleve_form.parent_id.choices += [(p.id, f"{p.prenom or ''} {p.nom or ''} ({p.telephone or 'Sans tel'})".strip()) for p in parents_form]

        # 2. Formulaire d'encaissement de scolarité
        form_paiement = PaiementForm()
        inscriptions_annee = (
            Inscription.query
            .options(joinedload(Inscription.eleve), joinedload(Inscription.classe))
            .filter(
                Inscription.ecole_id == ecole_id,
                Inscription.annee_scolaire_id == (annee_consultee.id if annee_consultee else 0),
            )
            .order_by(Inscription.classe_id, Inscription.eleve_id)
            .all()
        )
        form_paiement.eleve_id.choices = [
            (
                ins.eleve_id,
                f"{ins.eleve.nom} {ins.eleve.prenom} - {ins.classe.nom if ins.classe else 'Sans classe'}"
            )
            for ins in inscriptions_annee if ins.eleve
        ]
        if not form_paiement.eleve_id.choices:
            eleves_all = Eleve.query.filter_by(ecole_id=ecole_id).order_by(Eleve.nom, Eleve.prenom).all()
            form_paiement.eleve_id.choices = [(e.id, f"{e.nom} {e.prenom}") for e in eleves_all]

        # 3. Formulaire d'affectation de cours / emploi du temps
        emploi_form = AjouterEmploiForm(annee=annee_consultee)
        emploi_form.classe_id.choices = [(c.id, c.nom) for c in classes]
        professeurs = Professeur.query.filter_by(ecole_id=ecole_id).order_by(Professeur.nom).all()
        emploi_form.professeur_id.choices = [(p.id, f"{p.prenom} {p.nom}") for p in professeurs]
        if classes:
            cours_query = Cours.query.filter_by(ecole_id=ecole_id, classe_id=classes[0].id).order_by(Cours.nom).all()
            emploi_form.cours_id.choices = [(c.id, c.nom) for c in cours_query]
        else:
            emploi_form.cours_id.choices = []

        # 4. Suivi des Présences aujourd'hui
        today = date.today()
        absents_today = Absence.query.filter(
            Absence.ecole_id == ecole_id,
            Absence.date_absence == today
        ).count()
        total_eleves = stats.get('total_eleves', 0) if stats else 0
        if total_eleves > 0:
            presents_today = max(0, total_eleves - absents_today)
            taux_assiduite = round((presents_today / total_eleves) * 100, 1)
        else:
            presents_today = 0
            taux_assiduite = 100.0
        stats_presences_jour = {
            'total': total_eleves,
            'presents': presents_today,
            'absents': absents_today,
            'taux': taux_assiduite,
            'date': today
        }

        # 5. Derniers versements enregistrés (5 dernières transactions)
        derniers_paiements = (
            Paiement.query
            .options(
                joinedload(Paiement.eleve),
                joinedload(Paiement.inscription).joinedload(Inscription.classe)
            )
            .filter(
                Paiement.ecole_id == ecole_id,
                Paiement.statut != 'annule'
            )
            .order_by(Paiement.date_paiement.desc(), Paiement.id.desc())
            .limit(5)
            .all()
        )

        return render_template(
            'index.html',
            stats=stats,
            school_setup_state=setup_state,
            annee_consultee=annee_consultee,
            force_tour_prompt=force_tour_prompt,
            annee_planifiee=annee_planifiee,
            etat_planifiee=etat_planifiee,
            email_non_connecte=email_non_connecte,
            rappel_periode_suivante=rappel_periode_suivante,
            stats_presences_jour=stats_presences_jour,
            derniers_paiements=derniers_paiements,
            classes=classes,
            eleve_form=eleve_form,
            form_paiement=form_paiement,
            emploi_form=emploi_form,
            return_url=url_for('main.index'),
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
        from app.admin.scripts import get_maintenance_status
        maint = get_maintenance_status()
        is_maint = maint.get('active', False)
        is_sa = getattr(current_user, 'role', None) == 'super_admin'

        if request.args.get('switch') == '1' or (is_maint and not is_sa):
            logout_user()
            if is_maint:
                flash("La plateforme est en mode maintenance. Connectez-vous avec vos identifiants Super Administrateur.", "info")
            return redirect(url_for('main.login'))

        role = getattr(current_user, "role", None)
        endpoint_par_role = {
            "admin": "main.index",
            "super_admin": "main.index",
            "professeur": "main.professeur_dashboard",
            "parent": "main.parent_dashboard",
        }
        return redirect(url_for(endpoint_par_role.get(role, "main.index")))

    # /login doit rester une porte d'entree applicative stable, surtout apres
    # installation PWA ou expiration de session. La vitrine reste accessible
    # explicitement via la page d'accueil publique.

    form = LoginForm()
    active_login_type = request.form.get('login_type') or ('admin' if request.form.get('email') else 'terrain')
    if form.validate_on_submit():
        # sanitize + normaliser l'identifiant
        login_type = request.form.get('login_type') or ('admin' if request.form.get('email') else 'terrain')
        active_login_type = login_type if login_type in ('terrain', 'admin') else 'terrain'
        password = form.mot_de_passe.data
        identifiant = ""

        # Option: implementer un throttle/lockout par identifiant ici (compte)
        # Exemple (pseudo): if too_many_failed_attempts(identifiant): flash(...); return redirect(...)

        # Recherche utilisateur par email (case-insensitive) ou telephone
        # Assure-toi d'avoir les colonnes indexées pour la perf
        # IP via get_remote_address (plus fiable avec flask-limiter)
        ip = get_remote_address()
        utilisateur = None
        if active_login_type == 'admin':
            identifiant = (request.form.get('email') or form.telephone.data or '').strip().lower()
            if identifiant:
                utilisateur = Utilisateur.query.filter(Utilisateur.email.ilike(identifiant)).first()
            if utilisateur and utilisateur.role not in ("admin", "super_admin"):
                current_app.logger.warning(f"Connexion admin refusee pour role={utilisateur.role} identifiant={escape(identifiant)} depuis {ip}")
                utilisateur = None
                flash("Cet espace est reserve a la direction et a l'administration.", "warning")
        else:
            identifiant = (request.form.get('telephone') or form.telephone.data or '').strip()
            if "@" in identifiant:
                flash("Pour un compte administrateur, utilisez l'acces Direction & Administration en bas de page.", "info")
            else:
                utilisateur = _resolve_utilisateur_par_telephone(identifiant)
                if utilisateur and utilisateur.role in ("admin", "super_admin"):
                    current_app.logger.warning(f"Compte admin tente sur espace terrain id={utilisateur.id} depuis {ip}")
                    utilisateur = None
                    flash("Compte administrateur detecte : utilisez l'acces Direction & Administration en bas de page.", "info")
                elif utilisateur and utilisateur.role not in ("professeur", "parent", "eleve"):
                    current_app.logger.warning(f"Connexion terrain refusee pour role={utilisateur.role} identifiant={escape(identifiant)} depuis {ip}")
                    utilisateur = None
                    flash("Cet espace est reserve aux professeurs, parents et eleves.", "warning")

        if utilisateur and check_password_hash(utilisateur.mot_de_passe, password):
            # utilisateur existe et mot de passe correct

            # Vérification école pour tous sauf super_admin
            if utilisateur.role != "super_admin" and not utilisateur.ecole_id:
                flash("Votre compte n'est associé à aucune école. Contactez l'administrateur.", "danger")
                current_app.logger.warning(f"Connexion échouée (pas d'école) pour {identifiant} depuis {ip}")
                return redirect(url_for("main.login"))

            # Vérification école bloquée / suspendue
            if utilisateur.role != "super_admin" and utilisateur.ecole and utilisateur.ecole.statut in ('bloque', 'suspendu', 'inactive'):
                ecole = utilisateur.ecole
                if ecole.statut == 'inactive':
                    flash("Votre etablissement est actuellement desactive. Veuillez contacter l'administrateur de la plateforme.", "danger")
                elif utilisateur.role == 'admin':
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

            utilisateur.dernier_acces = datetime.utcnow()
            db.session.commit()

            login_user(utilisateur, remember=form.remember.data)
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

            endpoint_par_role = {
                "admin": "main.index",
                "super_admin": "main.index",
                "professeur": "main.professeur_dashboard",
                "parent": "main.parent_dashboard",
            }
            fallback = url_for(endpoint_par_role.get(utilisateur.role, "main.index"))
            next_page = sanitize_internal_url(request.args.get('next'), fallback)
            next_path = next_page.split('?', 1)[0].rstrip('/') or '/'
            if next_path in ('/login', '/logout'):
                next_page = fallback
            return redirect(next_page)
        else:
            # échec de connexion
            current_app.logger.warning(f"Tentative de connexion échouée pour identifiant={escape(identifiant)} depuis {ip}")
            if not any(category in ("info", "warning") for category, _ in session.get("_flashes", [])):
                flash('Identifiant ou mot de passe / code PIN incorrect.', 'danger')

    return render_template('login.html', form=form, active_login_type=active_login_type)

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

@main.route('/request_reset_password', methods=['GET', 'POST'])
@limiter.limit("5 per minute; 20 per day", key_func=get_remote_address)
def request_reset_password():
    """Page pour demander un lien de réinitialisation par email"""
    from app.forms import RequestResetPasswordForm
    from app.notifications import envoyer_email  # <-- Import correct, même que pour ajouter_eleve

    form = RequestResetPasswordForm()

    if form.validate_on_submit():
        email = form.email.data.lower()
        utilisateur = Utilisateur.query.filter_by(email=email).first()

        if utilisateur and utilisateur.role in ("admin", "super_admin"):
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

            email_ok = envoyer_email(utilisateur.email, sujet, message, context="reset_password")
            if email_ok:
                current_app.logger.info("EMAIL_SUCCESS_HANDLED type=reset_password recipient=%s", utilisateur.email)
            else:
                current_app.logger.warning("EMAIL_FAILED_HANDLED type=reset_password recipient=%s", utilisateur.email)
        else:
            current_app.logger.info(f"Tentative de reset pour email inexistant: {email}")

        # Message générique pour éviter de révéler l'existence d'un compte
        flash("Si un compte existe pour cet email, un lien de réinitialisation a été envoyé.", "info")
        return redirect(url_for('main.login'))

    return render_template('request_reset_password.html', form=form)

@main.route('/reset_password/<token>', methods=['GET', 'POST'])
@limiter.limit("10 per minute", key_func=get_remote_address)
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


@main.route('/demander-demo', methods=['GET', 'POST'])
@limiter.limit("6 per minute; 25 per hour", key_func=get_remote_address)
def demander_demo():
    """Prise de contact et demande de présentation pour les établissements scolaires"""
    if request.method == 'POST':
        is_json = request.is_json
        data = request.get_json() if is_json else request.form

        nom_ecole = escape((data.get('nom_ecole') or '').strip())
        telephone = escape((data.get('telephone') or '').strip())
        email = escape((data.get('email') or '').strip().lower())
        ville = escape((data.get('ville') or '').strip())
        message = escape((data.get('message') or '').strip())

        # HONEYPOT ANTI-SPAM
        honeypot = (data.get('website_url') or '').strip()
        if honeypot:
            current_app.logger.warning(f"SPAM BOT DÉTECTÉ sur /demander-demo via honeypot (IP: {request.remote_addr})")
            if is_json:
                return jsonify({'success': True, 'message': 'Votre demande de présentation a bien été transmise. Notre équipe vous contactera sous 24h.'}), 200
            flash("Votre demande a bien été transmise. Merci !", "success")
            return redirect(url_for('main.login'))

        # Validation minimale des coordonnées indispensables
        if not nom_ecole or not (telephone or email):
            err_msg = "Veuillez renseigner le nom de l'établissement et au moins un moyen de contact (téléphone ou email)."
            if is_json:
                return jsonify({'success': False, 'message': err_msg}), 400
            flash(err_msg, "warning")
            return render_template('demander_demo.html')

        # Enregistrement en base de données pour consultation Super Admin
        try:
            from app.models import DemandePresentation
            demande = DemandePresentation(
                nom_ecole=nom_ecole,
                telephone=telephone,
                email=email,
                ville=ville,
                message=message,
                statut='nouvelle'
            )
            db.session.add(demande)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Erreur enregistrement DemandePresentation : {e}")

        # Journalisation de la demande
        current_app.logger.info(
            f"DEMANDE_PRESENTATION_REÇUE : Établissement='{nom_ecole}', "
            f"Tél='{telephone}', Email='{email}', Ville='{ville}', Msg='{message[:120]}'"
        )

        success_msg = "Merci ! Votre demande de présentation a bien été enregistrée. Notre équipe vous contactera sous 24h ouvrées."
        if is_json:
            return jsonify({'success': True, 'message': success_msg})

        flash(success_msg, "success")
        return redirect(url_for('main.index'))

    return render_template('demander_demo.html')


@main.route('/politique-confidentialite')
def politique_confidentialite():
    """Page publique détaillant la politique de confidentialité et la protection des données scolaires"""
    return render_template('politique_confidentialite.html')


@main.route('/securite')
def securite():
    """Page publique détaillant les garanties de sécurité factuelles de KLASORA"""
    return render_template('securite.html')



# ====================================================================
# ROUTES SEO (Robots.txt & Sitemap.xml)
# ====================================================================
from flask import Response

@main.route('/robots.txt')
def robots_txt():
    content = "User-agent: *\nAllow: /\nSitemap: https://www.klasora.com/sitemap.xml\n"
    return Response(content, mimetype='text/plain')

@main.route('/sitemap.xml')
def sitemap_xml():
    content = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  <url>\n    <loc>https://www.klasora.com/</loc>\n  </url>\n</urlset>'
    return Response(content, mimetype='application/xml')
