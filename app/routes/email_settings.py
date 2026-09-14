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
from app.models import Ecole
from app.services.google_mail import (
    GoogleOAuthError,
    SchoolMailNotConfiguredError,
    SchoolMailSendError,
    connect_school_gmail,
    get_google_auth_url,
    get_school_mail_status,
    revoke_and_disconnect_school_gmail,
    send_school_email,
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
            f"Félicitations ! Votre compte Gmail ({connected_email}) a été connecté avec succès. "
            "Votre établissement enverra désormais tous ses e-mails scolaires depuis cette adresse ✅",
            "success",
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
    """
    Envoie un e-mail de test immédiat depuis le compte Gmail connecté
    vers ce même compte pour confirmer son bon fonctionnement.
    """
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    status = get_school_mail_status(current_user.ecole_id)
    if not status["is_connected"] or not status["email"]:
        flash(
            "Veuillez d'abord connecter votre compte Gmail avant d'envoyer un e-mail de test.",
            "warning",
        )
        return redirect(url_for("main.config_email"))

    ecole = Ecole.query.get(current_user.ecole_id)
    school_name = ecole.nom if ecole else "Votre établissement"
    recipient = status["email"]

    subject = f"✅ Test de connexion Gmail réussi - {school_name}"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
        <h2 style="color: #16a34a; margin-top: 0;">Connexion Gmail réussie !</h2>
        <p>Bonjour,</p>
        <p>Ce message confirme que le compte Gmail de votre établissement <strong>{school_name}</strong> est parfaitement connecté à <strong>KLASORA</strong>.</p>
        <div style="background-color: #f0fdf4; border-left: 4px solid #16a34a; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; color: #166534; font-size: 14px;">
                <strong>Adresse expéditrice configurée :</strong> {recipient}<br>
                <strong>Statut :</strong> Opérationnel et sécurisé
            </p>
        </div>
        <p>Vos bulletins scolaires, notifications d'absences et messages aux parents seront désormais acheminés directement depuis cette adresse sans passer par les dossiers de courriers indésirables.</p>
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 25px 0;">
        <p style="color: #6b7280; font-size: 12px; margin: 0;">
            Message envoyé automatiquement par KLASORA.
        </p>
    </div>
    """
    text_body = (
        f"Connexion Gmail réussie !\n\n"
        f"Ce message confirme que le compte Gmail de votre établissement {school_name} "
        f"est parfaitement connecté à KLASORA ({recipient})."
    )

    try:
        send_school_email(
            ecole_id=current_user.ecole_id,
            to=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        flash(
            f"E-mail de test envoyé avec succès à {recipient} ! "
            "Vérifiez votre boîte de réception Gmail (et les spams si besoin) ✉️",
            "success",
        )
    except SchoolMailNotConfiguredError as e:
        flash(str(e), "warning")
    except SchoolMailSendError as e:
        flash(f"Échec de l'envoi de l'e-mail de test : {e}", "danger")
    except Exception as e:
        current_app.logger.error(f"Erreur inattendue test email école {current_user.ecole_id}: {e}")
        flash(f"Une erreur est survenue lors de l'envoi du test : {e}", "danger")

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

