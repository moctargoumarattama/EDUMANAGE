from datetime import date, datetime, time

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from . import main
from .common import (
    Absence,
    Classe,
    Cours,
    EmploiTemps,
    Inscription,
    Note,
    Paiement,
    current_user,
    login_required,
    render_template,
    role_required,
    url_for,
)
from app.services.annees_scolaires import get_annee_active


@main.route("/point-du-jour")
@login_required
@role_required("admin")
def point_du_jour():
    ecole_id = current_user.ecole_id
    today = date.today()
    start = datetime.combine(today, time.min)
    end = datetime.combine(today, time.max)
    annee_active = get_annee_active(ecole_id)

    absences_today = Absence.query.filter_by(ecole_id=ecole_id, date_absence=today).count()

    cours_sans_prof = 0
    classes_sans_emploi = 0
    evaluations_incompletes = 0
    if annee_active:
        cours_sans_prof = Cours.query.join(Classe, Classe.id == Cours.classe_id).filter(
            Cours.ecole_id == ecole_id,
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee_active.id,
            Cours.professeur_id.is_(None),
        ).count()
        classes_sans_emploi = Classe.query.outerjoin(EmploiTemps, EmploiTemps.classe_id == Classe.id).filter(
            Classe.ecole_id == ecole_id,
            Classe.annee_scolaire_id == annee_active.id,
        ).group_by(Classe.id).having(func.count(EmploiTemps.id) == 0).count()
        evaluations_incompletes = Inscription.query.outerjoin(Note, Note.inscription_id == Inscription.id).filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_active.id,
            Inscription.statut == "inscrit",
        ).group_by(Inscription.id).having(func.count(Note.id) == 0).count()

    todo_items = [
        {"label": "cours sans professeur", "count": cours_sans_prof, "url": url_for("main.cours")},
        {"label": "évaluations incomplètes", "count": evaluations_incompletes, "url": url_for("main.notes")},
        {"label": "classes sans emploi du temps", "count": classes_sans_emploi, "url": url_for("main.admin_emplois")},
    ]
    todo_items = [item for item in todo_items if item["count"] > 0]

    paiements = Paiement.query.options(joinedload(Paiement.inscription).joinedload(Inscription.eleve)).filter(
        Paiement.ecole_id == ecole_id,
        Paiement.date_paiement >= start,
        Paiement.date_paiement <= end,
    ).order_by(Paiement.date_paiement.desc()).limit(50).all()
    absences = Absence.query.options(joinedload(Absence.eleve), joinedload(Absence.cours)).filter(
        Absence.ecole_id == ecole_id,
        Absence.date_absence == today,
    ).order_by(Absence.id.desc()).limit(50).all()
    notes = Note.query.options(joinedload(Note.cours).joinedload(Cours.classe)).filter(
        Note.ecole_id == ecole_id,
        Note.date_evaluation >= start,
        Note.date_evaluation <= end,
    ).order_by(Note.date_evaluation.desc()).limit(50).all()

    raw_events = []
    for paiement in paiements:
        eleve = paiement.inscription.eleve if paiement.inscription else None
        raw_events.append({
            "time": paiement.date_paiement,
            "show_time": True,
            "title": "Paiement reçu",
            "detail": f"{eleve.prenom} {eleve.nom}" if eleve else "Élève",
            "meta": f"{paiement.montant:,.0f} XOF".replace(",", " "),
            "url": url_for("main.paiements"),
        })
    for absence in absences:
        raw_events.append({
            "time": datetime.combine(absence.date_absence, time.min),
            "show_time": False,
            "title": "Absence enregistrée",
            "detail": f"{absence.eleve.prenom} {absence.eleve.nom}" if absence.eleve else "Élève",
            "meta": absence.cours.nom if absence.cours else "",
            "url": url_for("main.absences"),
        })
    for note in notes:
        raw_events.append({
            "time": note.date_evaluation,
            "show_time": True,
            "title": "Note ajoutée",
            "detail": note.cours.nom if note.cours else "Évaluation",
            "meta": note.cours.classe.nom if note.cours and note.cours.classe else "",
            "url": url_for("main.notes"),
        })

    grouped = {}
    for event in raw_events:
        minute = event["time"].replace(second=0, microsecond=0) if event.get("time") and event["show_time"] else None
        key = (minute, event["show_time"], event["title"], event["detail"], event["meta"], event["url"])
        if key not in grouped:
            grouped[key] = {**event, "count": 0}
        grouped[key]["count"] += 1

    events = sorted(grouped.values(), key=lambda item: item["time"] or start, reverse=True)[:20]
    for event in events:
        if event["count"] <= 1:
            event["display_title"] = event["title"]
        elif event["title"] == "Note ajoutée":
            event["display_title"] = f"{event['count']} notes ajoutées"
        elif event["title"] == "Paiement reçu":
            event["display_title"] = f"{event['count']} paiements reçus"
        elif event["title"] == "Absence enregistrée":
            event["display_title"] = f"{event['count']} absences enregistrées"
        else:
            event["display_title"] = f"{event['count']} {event['title'].lower()}"

    return render_template(
        "point_du_jour.html",
        today=today,
        annee_active=annee_active,
        counters={"absences": absences_today},
        todo_items=todo_items,
        events=events,
    )
