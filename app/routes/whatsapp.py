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

def _get_gateway_headers():
    secret = os.environ.get("WHATSAPP_GATEWAY_SECRET", "secret-gateway-local-klasora-2024")
    return {"X-Gateway-Secret": secret}

def _sanitize_gateway_payload(payload):
    if not isinstance(payload, dict):
        return {"status": "INDISPONIBLE", "connected": False}

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


def sync_ecole_whatsapp_enabled(ecole_id, is_connected):
    from app import db
    from app.models import Ecole
    ecole = db.session.get(Ecole, ecole_id)
    if ecole:
        # Check current manual state, only update if it matches connection state changes?
        # Actually, the instructions say: atomiquement ecole.whatsapp_enabled = True.
        if is_connected and not ecole.whatsapp_enabled:
            ecole.whatsapp_enabled = True
            db.session.commit()
        elif not is_connected and ecole.whatsapp_enabled:
            ecole.whatsapp_enabled = False
            db.session.commit()

def get_whatsapp_gateway_status(ecole_id=None, include_qr=True):
    ecole_id = ecole_id or getattr(current_user, "ecole_id", None)
    if not ecole_id:
        return {"status": "INDISPONIBLE", "connected": False}
    endpoint = "qr" if include_qr else "status"
    try:
        response = requests.get(
            _gateway_url(f"/session/{ecole_id}/{endpoint}"),
            headers=_get_gateway_headers(),
            timeout=WHATSAPP_GATEWAY_TIMEOUT
        )
        response.raise_for_status()
        payload = _sanitize_gateway_payload(response.json())
        sync_ecole_whatsapp_enabled(ecole_id, payload.get("connected", False))
        return payload
    except (requests.RequestException, ValueError, TypeError) as exc:
        current_app.logger.warning("Passerelle WhatsApp indisponible: %s", exc)
        sync_ecole_whatsapp_enabled(ecole_id, False)
        return {"status": "INDISPONIBLE", "connected": False}


@main.route("/parametres/whatsapp", methods=["GET"])
@main.route("/admin/whatsapp", methods=["GET"])
@login_required
def whatsapp_connect():
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    whatsapp_status = get_whatsapp_gateway_status(current_user.ecole_id, include_qr=True)
    from app import db
    from app.models import Ecole
    ecole = db.session.get(Ecole, current_user.ecole_id)
    return render_template("admin/whatsapp.html", whatsapp_status=whatsapp_status, ecole=ecole)


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
            headers=_get_gateway_headers(),
            timeout=WHATSAPP_GATEWAY_TIMEOUT,
        )
        response.raise_for_status()
        sync_ecole_whatsapp_enabled(current_user.ecole_id, False)
        flash_message = "Session WhatsApp deconnectee."
        category = "success"
    except requests.RequestException as exc:
        current_app.logger.warning("Deconnexion WhatsApp impossible: %s", exc)
        sync_ecole_whatsapp_enabled(current_user.ecole_id, False)
        flash_message = "Passerelle WhatsApp indisponible."
        category = "warning"

    from flask import flash
    flash(flash_message, category)
@main.route("/parametres/whatsapp/toggle", methods=["POST"])
@main.route("/admin/whatsapp/toggle", methods=["POST"])
@login_required
def whatsapp_toggle():
    ok, redirect_resp = check_school_admin_access()
    if not ok:
        return redirect_resp

    from app import db
    from app.models import Ecole
    from flask import request, flash
    ecole = db.session.get(Ecole, current_user.ecole_id)
    if ecole:
        enabled = request.form.get("whatsapp_enabled") == "1"
        ecole.whatsapp_enabled = enabled
        db.session.commit()
        if enabled:
            flash("Notifications WhatsApp activées.", "success")
        else:
            flash("Notifications WhatsApp en pause.", "warning")

    return redirect(url_for("main.whatsapp_connect"))
