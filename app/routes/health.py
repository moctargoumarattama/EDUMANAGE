from sqlalchemy import text

from . import main
from .common import db, jsonify


@main.route('/health')
def health():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "ok", "database": "ok"}), 200
    except Exception:
        return jsonify({"status": "error", "database": "unavailable"}), 503
