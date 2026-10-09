"""
Routes pour la configuration et la gestion de Gmail par École via OAuth 2.0.
"""

import secrets
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from . import main
from app.models import Ecole, db
from app.services.google_mail import (
    GoogleOAuthError,
    connect_school_gmail,
    get_google_auth_url,
    get_school_mail_status,
    revoke_and_disconnect_school_gmail,
)

GOOGLE_OAUTH_STATE_SALT = "google-mail-oauth-state"
GOOGLE_OAUTH_STATE_MAX_AGE = 600


class GoogleOAuthStateError(ValueError):
    pass


def _state_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=GOOGLE_OAUTH_STATE_SALT)


def generate_google_oauth_state(user_id, ecole_id):
    payload = {
        "user_id": int(user_id),
        "ecole_id": int(ecole_id),
        "nonce": secrets.token_urlsafe(24),
    }
    return _state_serializer().dumps(payload)


def validate_google_oauth_state(state, user_id, ecole_id, max_age=GOOGLE_OAUTH_STATE_MAX_AGE):
    if not state:
        raise GoogleOAuthStateError("state_absent")
    try:
        payload = _state_serializer().loads(state, max_age=max_age)
    except SignatureExpired as exc:
        raise GoogleOAuthStateError("state_expire") from exc
    except BadSignature as exc:
        raise GoogleOAuthStateError("state_invalide") from exc

    if int(payload.get("user_id", 0)) != int(user_id):
        raise GoogleOAuthStateError("user_invalide")
    if int(payload.get("ecole_id", 0)) != int(ecole_id):
        raise GoogleOAuthStateError("ecole_invalide")
    if not payload.get("nonce"):
        raise GoogleOAuthStateError("nonce_absent")
    return payload


def check_school_admin_access():
    """
    Vérifie que l'utilisateur connecté est un administrateur d'établissement
    ayant une école associée valide.
    """
    if current_user.role == "super_admin":
        flash(
            "Les super administrateurs supervisent la plateforme KLASORA. "
            "Pour connecter le Gmail d'un établissement, veuillez vous connecter avec le compte administrateur de cet établissement.",
            "info",
        )
        return False, redirect(url_for("main.gestion_ecoles"))

    if current_user.role != "admin":
        flash("Accès réservé aux administrateurs d'établissement.", "danger")
        return False, redirect(url_for("main.dashboard"))

    if not current_user.ecole_id:
        flash("Aucun établissement n'est associé à votre compte administrateur.", "danger")
        return False, redirect(url_for("main.dashboard"))

    return True, None


@main.route("/parametres/email", methods=["GET"])
@login_required
def config_email():
    """
    Page de configuration Gmail de l'établissement :
    Affiche l'état de connexion (connecté ou non) avec interface épurée en 1 clic.
    """
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    ecole = Ecole.query.get_or_404(current_user.ecole_id)
    mail_status = get_school_mail_status(ecole.id)

    return render_template(
        "admin/config_email.html",
        ecole=ecole,
        mail_status=mail_status,
    )


@main.route("/google/mail/connect", methods=["GET"])
@login_required
def google_mail_connect():
    """
    Démarre le flux OAuth 2.0 Google pour l'établissement de l'administrateur.
    Génère un état anti-CSRF aléatoire et redirige vers Google.
    """
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    state = generate_google_oauth_state(current_user.id, current_user.ecole_id)
    session["google_oauth_state"] = state
    session["google_oauth_ecole_id"] = current_user.ecole_id

    try:
        auth_url = get_google_auth_url(state)
        return redirect(auth_url)
    except GoogleOAuthError as e:
        current_app.logger.error(f"Google OAuth non configuré: {e}")
        flash(
            "Le service de connexion Google est en cours d'initialisation. Veuillez réessayer dans quelques instants.",
            "info",
        )
        return redirect(url_for("main.config_email"))
    except Exception as e:
        current_app.logger.error(f"Erreur initialisation Google OAuth: {e}")
        flash("Une erreur inattendue est survenue lors de l'accès à Google.", "danger")
        return redirect(url_for("main.config_email"))


@main.route("/google/mail/callback", methods=["GET"])
@login_required
def google_mail_callback():
    """
    Point de retour après consentement de l'utilisateur sur Google.
    Vérifie l'état anti-CSRF, échange le code contre les tokens,
    chiffre les données sensibles et enregistre la liaison.
    """
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    error = request.args.get("error")
    if error:
        current_app.logger.warning(f"Google OAuth callback annulé/erreur: {error}")
        flash("La connexion à votre compte Google a été annulée ou refusée.", "warning")
        return redirect(url_for("main.config_email"))

    received_state = request.args.get("state")
    session.pop("google_oauth_state", None)
    session.pop("google_oauth_ecole_id", None)

    try:
        validate_google_oauth_state(received_state, current_user.id, current_user.ecole_id)
    except GoogleOAuthStateError:
        current_app.logger.warning("Échec validation anti-CSRF callback Google OAuth.")
        flash("La vérification de sécurité (CSRF) a échoué. Veuillez recommencer la connexion.", "danger")
        return redirect(url_for("main.config_email"))

    code = request.args.get("code")
    if not code:
        flash("Aucun code d'autorisation n'a été fourni par Google.", "danger")
        return redirect(url_for("main.config_email"))

    try:
        connected_email = connect_school_gmail(current_user.ecole_id, code)
        flash(
            f"Compte Gmail ({connected_email}) connecté. Le canal d'envoi scolaire reste désactivé.",
            "info",
        )
    except GoogleOAuthError as e:
        flash(f"Impossible de finaliser la connexion Google : {e}", "danger")
    except Exception as e:
        current_app.logger.error(f"Erreur traitement callback Google OAuth: {e}")
        flash("Une erreur inattendue est survenue lors de l'enregistrement de votre compte Gmail.", "danger")

    return redirect(url_for("main.config_email"))


@main.route("/parametres/email/test", methods=["POST"])
@login_required
def envoyer_email_test_ecole():
    """Ancien test email ecole, desactive au profit de WhatsApp."""
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp
    flash("Le canal email ecole est desactive. Les notifications terrain passent par WhatsApp.", "info")
    return redirect(url_for("main.config_email"))

@main.route("/parametres/email/disconnect", methods=["POST"])
@login_required
def deconnecter_gmail_ecole():
    """
    Déconnecte le compte Gmail de l'établissement, révoque le jeton Google
    et supprime les tokens chiffrés en base de données.
    """
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    try:
        revoke_and_disconnect_school_gmail(current_user.ecole_id)
        flash(
            "Le compte Gmail de l'établissement a été déconnecté avec succès. "
            "Aucun e-mail scolaire ne pourra être envoyé tant qu'un compte n'est pas reconnecté.",
            "info",
        )
    except Exception as e:
        current_app.logger.error(f"Erreur déconnexion Gmail école {current_user.ecole_id}: {e}")
        flash("Une erreur est survenue lors de la déconnexion.", "danger")

    return redirect(url_for("main.config_email"))

