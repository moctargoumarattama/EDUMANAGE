"""Secret privé partagé entre Flask et la passerelle WhatsApp locale."""

import os
import secrets
from pathlib import Path

from flask import current_app, has_app_context


SECRET_PATH = Path(__file__).resolve().parents[2] / "instance" / "whatsapp_gateway_secret"

def get_gateway_secret():
    configured = (os.environ.get("WHATSAPP_GATEWAY_SECRET") or "").strip()
    if configured:
        if len(configured) < 32:
            raise RuntimeError("WHATSAPP_GATEWAY_SECRET doit contenir au moins 32 caractères.")
        return configured

    # Les tests ne doivent pas créer de secret sur le disque du projet.
    if has_app_context() and current_app.config.get("TESTING"):
        return "klasora-test-gateway-secret-memory-only"

    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with SECRET_PATH.open("x", encoding="utf-8") as secret_file:
            os.chmod(SECRET_PATH, 0o600)
            secret_file.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    secret = SECRET_PATH.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise RuntimeError("Secret de passerelle local incomplet ; vérifiez son fichier privé.")
    return secret
