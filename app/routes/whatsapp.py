import os

import requests
from flask import current_app, jsonify, make_response, render_template
from flask_login import login_required

from . import main
from .email_settings import check_school_admin_access


WHATSAPP_GATEWAY_QR_URL = os.environ.get("WHATSAPP_GATEWAY_QR_URL", "http://127.0.0.1:3001/qr")
WHATSAPP_GATEWAY_TIMEOUT = 2
WHATSAPP_ALLOWED_STATUSES = {"CONNECTE", "ATTENTE_SCAN", "DECONNECTE"}


def _sanitize_gateway_payload(payload):
    if not isinstance(payload, dict):
        return {"status": "INDISPONIBLE"}

    status = payload.get("status")
    if status not in WHATSAPP_ALLOWED_STATUSES:
        status = "INDISPONIBLE"

    result = {"status": status}
    qr = payload.get("qr")
    if status == "ATTENTE_SCAN" and isinstance(qr, str) and qr.startswith("data:image/png;base64,"):
        result["qr"] = qr
    return result


def get_whatsapp_gateway_status():
    try:
        response = requests.get(WHATSAPP_GATEWAY_QR_URL, timeout=WHATSAPP_GATEWAY_TIMEOUT)
        response.raise_for_status()
        return _sanitize_gateway_payload(response.json())
    except (requests.RequestException, ValueError, TypeError) as exc:
        current_app.logger.warning("Passerelle WhatsApp indisponible: %s", exc)
        return {"status": "INDISPONIBLE"}


@main.route("/admin/whatsapp", methods=["GET"])
@login_required
def whatsapp_connect():
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    whatsapp_status = get_whatsapp_gateway_status()
    return render_template("admin/whatsapp_connect.html", whatsapp_status=whatsapp_status)


@main.route("/admin/whatsapp/status", methods=["GET"])
@login_required
def whatsapp_status():
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    response = make_response(jsonify(get_whatsapp_gateway_status()))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response
