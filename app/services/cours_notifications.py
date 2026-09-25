"""
Service de notification par e-mail pour l'assignation des cours aux professeurs.
Utilise le compte Gmail connecté de l'établissement (via Google OAuth 2.0 / parametres/email).
"""

import logging
import threading
from typing import Optional
from flask import current_app, render_template

from app import db
from app.models import Ecole, Professeur, Cours
from app.services.google_mail import (
    SchoolMailNotConfiguredError,
    SchoolMailSendError,
    get_school_mail_status,
    send_school_email,
)

logger = logging.getLogger(__name__)


def _send_cours_notification_worker(
    app_obj,
    ecole_id: int,
    professeur_email: str,
    school_email: Optional[str],
    subject: str,
    html_body: str,
):
    """
    Exécuteur en arrière-plan (worker thread) pour distribuer l'email via Gmail API.
    Option B : Envoie au professeur et envoie une copie d'archivage à l'école si disponible.
    """
    with app_obj.app_context():
        try:
            # 1. Envoi au professeur
            if professeur_email:
                send_school_email(
                    ecole_id=ecole_id,
                    to=professeur_email,
                    subject=subject,
                    html_body=html_body,
                )
                logger.info(
                    "EMAIL_SUCCESS_HANDLED type=cours_professeur_notification ecole_id=%s recipient=%s",
                    ecole_id,
                    professeur_email,
                )

            # 2. Copie d'archivage pour l'école (si différente et valide)
            if school_email and school_email.strip().lower() != professeur_email.strip().lower():
                copy_subject = f"[Copie École] {subject}"
                try:
                    send_school_email(
                        ecole_id=ecole_id,
                        to=school_email.strip(),
                        subject=copy_subject,
                        html_body=html_body,
                    )
                    logger.info(
                        "EMAIL_SUCCESS_HANDLED type=cours_professeur_copy_ecole ecole_id=%s recipient=%s",
                        ecole_id,
                        school_email,
                    )
                except Exception as copy_err:
                    logger.warning(
                        "Erreur lors de l'envoi de la copie école de notification cours (ecole_id=%s): %s",
                        ecole_id,
                        copy_err,
                    )

        except SchoolMailNotConfiguredError:
            logger.info(
                "Notification cours ignorée: Gmail non configuré dans parametres/email pour l'école id=%s",
                ecole_id,
            )
        except SchoolMailSendError as err:
            logger.warning(
                "Échec d'envoi Gmail API pour la notification cours (ecole_id=%s): %s",
                ecole_id,
                err,
            )
        except Exception as exc:
            logger.exception(
                "Erreur inattendue lors de l'envoi de la notification cours (ecole_id=%s): %s",
                ecole_id,
                exc,
            )


def notifier_professeur_cours_assigne(
    cours: Cours,
    professeur: Professeur,
    async_send: bool = True,
) -> bool:
    """
    Prépare et déclenche la notification e-mail lorsqu'un cours est assigné à un professeur.
    Option B : Envoie l'e-mail au professeur et une copie sur le mail de l'école.
    
    :param cours: Instance du modèle Cours.
    :param professeur: Instance du modèle Professeur.
    :param async_send: Si True (par défaut), l'envoi s'exécute dans un thread d'arrière-plan.
    :return: True si la notification a été soumise ou préparée, False en cas d'impossibilité.
    """
    if not cours or not professeur:
        return False

    ecole_id = cours.ecole_id or professeur.ecole_id
    if not ecole_id:
        return False

    # Déterminer l'adresse email du professeur
    prof_email = (professeur.email or "").strip()
    if not prof_email and getattr(professeur, "utilisateur", None):
        prof_email = (professeur.utilisateur.email or "").strip()

    if not prof_email:
        logger.info(
            "Notification cours ignorée: Aucun email renseigné pour le professeur id=%s (%s %s)",
            professeur.id,
            professeur.prenom,
            professeur.nom,
        )
        return False

    # Vérifier l'état de la messagerie de l'école
    mail_status = get_school_mail_status(ecole_id)
    if not mail_status.get("is_connected"):
        logger.info(
            "Notification cours ignorée: Gmail non connecté dans parametres/email pour l'école id=%s",
            ecole_id,
        )
        return False

    # Récupérer l'adresse email administrative de l'école pour copie archivage
    ecole = db.session.get(Ecole, ecole_id) if ecole_id else None
    school_name = ecole.nom if ecole else "Établissement scolaire"
    school_email = (ecole.email or "").strip() if ecole else None
    if not school_email and mail_status.get("email"):
        school_email = mail_status.get("email")

    classe_nom = cours.classe.nom if (cours.classe and cours.classe.nom) else "Classe"
    annee_nom = ""
    if cours.classe and cours.classe.annee_scolaire:
        annee_nom = cours.classe.annee_scolaire.nom or ""

    subject = f"Attribution d'enseignement : {cours.nom} - {classe_nom} ({school_name})"

    html_body = render_template(
        "emails/cours_assigne_professeur.html",
        professeur=professeur,
        cours=cours,
        classe=cours.classe,
        ecole=ecole,
        school_name=school_name,
        annee_nom=annee_nom,
    )

    if async_send:
        app_obj = current_app._get_current_object()
        t = threading.Thread(
            target=_send_cours_notification_worker,
            args=(
                app_obj,
                ecole_id,
                prof_email,
                school_email,
                subject,
                html_body,
            ),
            daemon=True,
        )
        t.start()
        return True
    else:
        # Envoi synchrone (utile pour les tests unitaires)
        _send_cours_notification_worker(
            current_app._get_current_object(),
            ecole_id,
            prof_email,
            school_email,
            subject,
            html_body,
        )
        return True

