import os

import requests
from flask import current_app, jsonify, make_response, redirect, render_template, url_for
from flask_login import current_user, login_required

from . import main
from .email_settings import check_school_admin_access


WHATSAPP_GATEWAY_BASE_URL = os.environ.get("WHATSAPP_GATEWAY_BASE_URL", "http://127.0.0.1:3001")
WHATSAPP_GATEWAY_TIMEOUT = 2
WHATSAPP_ALLOWED_STATUSES = {"CONNECTE", "ATTENTE_SCAN", "DECONNECTE", "INDISPONIBLE"}


def _gateway_url(path):
    return f"{WHATSAPP_GATEWAY_BASE_URL.rstrip('/')}{path}"


def _sanitize_gateway_payload(payload):
    if not isinstance(payload, dict):
        return {"status": "INDISPONIBLE"}

    status = payload.get("status")
    if status not in WHATSAPP_ALLOWED_STATUSES:
        status = "INDISPONIBLE"

    result = {
        "status": status,
        "connected": bool(payload.get("connected")) if status != "INDISPONIBLE" else False,
    }
    phone = payload.get("phone") or payload.get("numero") or payload.get("sender")
    if isinstance(phone, str) and phone.strip():
        result["phone"] = phone.strip()
    qr = payload.get("qrImage") or payload.get("qr")
    if status == "ATTENTE_SCAN" and isinstance(qr, str) and qr.startswith("data:image/png;base64,"):
        result["qrImage"] = qr
    return result


def get_whatsapp_gateway_status(ecole_id=None, include_qr=True):
    ecole_id = ecole_id or getattr(current_user, "ecole_id", None)
    if not ecole_id:
        return {"status": "INDISPONIBLE", "connected": False}
    endpoint = "qr" if include_qr else "status"
    try:
        response = requests.get(_gateway_url(f"/session/{ecole_id}/{endpoint}"), timeout=WHATSAPP_GATEWAY_TIMEOUT)
        response.raise_for_status()
        return _sanitize_gateway_payload(response.json())
    except (requests.RequestException, ValueError, TypeError) as exc:
        current_app.logger.warning("Passerelle WhatsApp indisponible: %s", exc)
        return {"status": "INDISPONIBLE", "connected": False}


@main.route("/parametres/whatsapp", methods=["GET"])
@main.route("/admin/whatsapp", methods=["GET"])
@login_required
def whatsapp_connect():
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    whatsapp_status = get_whatsapp_gateway_status(current_user.ecole_id, include_qr=True)
    return render_template("admin/whatsapp.html", whatsapp_status=whatsapp_status)


@main.route("/parametres/whatsapp/status", methods=["GET"])
@main.route("/admin/whatsapp/status", methods=["GET"])
@login_required
def whatsapp_status():
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    response = make_response(jsonify(get_whatsapp_gateway_status(current_user.ecole_id, include_qr=True)))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@main.route("/parametres/whatsapp/logout", methods=["POST"])
@main.route("/admin/whatsapp/logout", methods=["POST"])
@login_required
def whatsapp_logout():
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    try:
        response = requests.post(
            _gateway_url(f"/session/{current_user.ecole_id}/logout"),
            timeout=WHATSAPP_GATEWAY_TIMEOUT,
        )
        response.raise_for_status()
        flash_message = "Session WhatsApp deconnectee."
        category = "success"
    except requests.RequestException as exc:
        current_app.logger.warning("Deconnexion WhatsApp impossible: %s", exc)
        flash_message = "Passerelle WhatsApp indisponible."
        category = "warning"

    from flask import flash
    flash(flash_message, category)
    return redirect(url_for("main.whatsapp_connect"))
