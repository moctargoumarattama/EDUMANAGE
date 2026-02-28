from flask import Blueprint, request, jsonify
from app.models import db, SyncLog, Eleve, Note, Absence, Classe
from datetime import datetime


api_sync = Blueprint("api_sync", __name__)

@api_sync.route("/api/sync", methods=["POST"])
def sync_data():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Aucune donnée reçue"}), 400

    log = SyncLog(data=data, status="pending")
    db.session.add(log)
    db.session.flush()

    imported = 0
    try:
        for item in data.get("items", []):
            typ = item.get("type")
            p = item.get("payload", {})
            if typ == "eleve":
                e = Eleve.query.filter_by(matricule=p.get("matricule")).first()
                if not e:
                    e = Eleve(
                        matricule=p.get("matricule"),
                        nom=p.get("nom"),
                        prenom=p.get("prenom")
                    )
                    if p.get("classe_nom"):
                        c = Classe.query.filter_by(nom=p.get("classe_nom")).first()
                        if c:
                            e.classe_id = c.id
                    db.session.add(e)
                    imported += 1
            elif typ == "note":
                e = Eleve.query.filter_by(matricule=p.get("matricule")).first()
                if e:
                    n = Note(eleve_id=e.id, matiere=p.get("matiere"), valeur=p.get("valeur"))
                    db.session.add(n)
                    imported += 1
            elif typ == "absence":
                e = Eleve.query.filter_by(matricule=p.get("matricule")).first()
                if e:
                    a = Absence(eleve_id=e.id, date=p.get("date"), motif=p.get("motif"))
                    db.session.add(a)
                    imported += 1

        db.session.commit()
        log.status = "processed"
        log.processed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"status": "ok", "imported": imported}), 200
    except Exception as e:
        db.session.rollback()
        log.status = "error"
        log.processed_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"error": str(e)}), 500
