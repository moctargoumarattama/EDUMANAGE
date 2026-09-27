from flask import current_app

from app import db
from app.models import Ecole
from app.services.phone_numbers import normaliser_numero_whatsapp
from app.services.whatsapp_queue import enqueue_message


def notifier_professeur_affectation_cours(professeur, cours, ecole=None, commit=True):
    try:
        if not professeur or not cours:
            return None

        ecole = ecole or getattr(professeur, "ecole", None) or db.session.get(Ecole, professeur.ecole_id)
        if not ecole or not getattr(ecole, "whatsapp_enabled", False):
            return None

        tel_prof = normaliser_numero_whatsapp(getattr(professeur, "telephone", None))
        if not tel_prof:
            current_app.logger.warning(
                "Notification WhatsApp affectation cours ignoree: telephone absent professeur_id=%s",
                getattr(professeur, "id", None),
            )
            return None

        classe = getattr(cours, "classe", None)
        classe_nom = getattr(classe, "nom", None) or "classe non renseignee"
        annee_nom = getattr(getattr(classe, "annee_scolaire", None), "nom", None)
        suffixe_annee = f" pour l'annee scolaire {annee_nom}" if annee_nom else ""
        nom_prof = f"{professeur.prenom or ''} {professeur.nom or ''}".strip() or "professeur"
        message = (
            f"Bonjour {nom_prof}, KLASORA vous informe qu'un nouveau cours vous a ete assigne "
            f"a {ecole.nom} : {cours.nom} - {classe_nom}{suffixe_annee}. "
            "Connectez-vous a votre espace pour consulter vos classes."
        )
        return enqueue_message(
            ecole_id=ecole.id,
            destinataire=tel_prof,
            message=message,
            type_message="general",
            commit=commit,
        )
    except Exception as exc:
        current_app.logger.warning(
            "Notification WhatsApp affectation cours ignoree professeur_id=%s cours_id=%s: %s",
            getattr(professeur, "id", None),
            getattr(cours, "id", None),
            exc,
        )
        return None
