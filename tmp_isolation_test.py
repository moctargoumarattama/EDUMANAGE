import logging
import uuid
from datetime import date, datetime

logging.disable(logging.CRITICAL)

from app import create_app
from app.extensions import db
from app.models import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    Paiement,
    Professeur,
    Utilisateur,
)


app = create_app()
ctx = app.app_context()
ctx.push()
client = None
mark = "ISO" + uuid.uuid4().hex[:8]


def add(obj):
    db.session.add(obj)
    return obj


def login(user):
    global client
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def hit(label, path, method="GET", json=None):
    resp = client.open(path, method=method, json=json, follow_redirects=False)
    print(label, path, resp.status_code)
    return resp.status_code


try:
    eA = add(Ecole(nom=mark + " A", statut="active"))
    eB = add(Ecole(nom=mark + " B", statut="active"))
    db.session.flush()

    aA = add(Utilisateur(nom=mark + "AdminA", prenom="T", email=mark + "a@test.local", role="admin", ecole_id=eA.id, statut="actif", mot_de_passe="x"))
    aB = add(Utilisateur(nom=mark + "AdminB", prenom="T", email=mark + "b@test.local", role="admin", ecole_id=eB.id, statut="actif", mot_de_passe="x"))
    pUser = add(Utilisateur(nom=mark + "ProfA", prenom="T", email=mark + "p@test.local", role="professeur", ecole_id=eA.id, statut="actif", mot_de_passe="x"))
    parentA = add(Utilisateur(nom=mark + "ParentA", prenom="T", email=mark + "pa@test.local", role="parent", ecole_id=eA.id, statut="actif", mot_de_passe="x"))
    parentB = add(Utilisateur(nom=mark + "ParentB", prenom="T", email=mark + "pb@test.local", role="parent", ecole_id=eB.id, statut="actif", mot_de_passe="x"))
    db.session.flush()

    profA = add(Professeur(nom=mark + "ProfA", prenom="T", email=mark + "p@test.local", code_prof=mark + "P", utilisateur_id=pUser.id, ecole_id=eA.id))
    yA = add(AnneeScolaire(nom=mark + " 2090A", date_debut=date(2090, 9, 1), date_fin=date(2091, 6, 30), statut="active", ecole_id=eA.id))
    yB = add(AnneeScolaire(nom=mark + " 2090B", date_debut=date(2090, 9, 1), date_fin=date(2091, 6, 30), statut="active", ecole_id=eB.id))
    db.session.flush()

    cA = add(Classe(nom=mark + "A1", niveau="T", ecole_id=eA.id, annee_scolaire_id=yA.id, capacite_max=30))
    cA2 = add(Classe(nom=mark + "A2", niveau="T", ecole_id=eA.id, annee_scolaire_id=yA.id, capacite_max=30))
    cB = add(Classe(nom=mark + "B1", niveau="T", ecole_id=eB.id, annee_scolaire_id=yB.id, capacite_max=30))
    db.session.flush()
    db.session.execute(
        db.text(
            "INSERT INTO professeur_classes (professeur_id, classe_id, ecole_id, date_assignation) "
            "VALUES (:professeur_id, :classe_id, :ecole_id, :date_assignation)"
        ),
        {
            "professeur_id": profA.id,
            "classe_id": cA.id,
            "ecole_id": eA.id,
            "date_assignation": datetime.utcnow(),
        },
    )

    elA1 = add(Eleve(nom=mark + "EleveA1", prenom="T", date_naissance=date(2015, 1, 1), ecole_id=eA.id, classe_id=cA.id, parent_id=parentA.id, frais_annuels=1))
    elA2 = add(Eleve(nom=mark + "EleveA2", prenom="T", date_naissance=date(2015, 1, 1), ecole_id=eA.id, classe_id=cA.id, parent_id=parentA.id, frais_annuels=1))
    elA3 = add(Eleve(nom=mark + "EleveA3", prenom="T", date_naissance=date(2015, 1, 1), ecole_id=eA.id, classe_id=cA2.id, frais_annuels=1))
    elB = add(Eleve(nom=mark + "EleveB", prenom="T", date_naissance=date(2015, 1, 1), ecole_id=eB.id, classe_id=cB.id, parent_id=parentB.id, frais_annuels=1))
    db.session.flush()

    coA = add(Cours(nom=mark + "CoursA", ecole_id=eA.id, classe_id=cA.id, professeur_id=profA.id, coefficient=1))
    coB = add(Cours(nom=mark + "CoursB", ecole_id=eB.id, classe_id=cB.id, coefficient=1))
    db.session.flush()

    nB = add(Note(valeur=12, coefficient=1, eleve_id=elB.id, cours_id=coB.id, ecole_id=eB.id, annee_id=yB.id, date_evaluation=datetime.utcnow()))
    abB = add(Absence(eleve_id=elB.id, cours_id=coB.id, ecole_id=eB.id, date_absence=date.today()))
    payB = add(Paiement(eleve_id=elB.id, ecole_id=eB.id, montant=1, mois="Janvier", annee=2090))
    db.session.commit()
    assoc_a2 = db.session.execute(
        db.text("SELECT * FROM professeur_classes WHERE professeur_id=:p AND classe_id=:c"),
        {"p": profA.id, "c": cA2.id},
    ).all()
    print("DEBUG_ASSIGN", "user", pUser.id, pUser.role, "prof", profA.id, "cA", cA.id, "cA_prof", cA.professeur_id, "cA2", cA2.id, "cA2_prof", cA2.professeur_id, "assigned", [c.id for c in profA.classes_assignees.all()], "assoc_a2", assoc_a2)

    login(aA)
    hit("adminA_eleveB", f"/voir_eleve/{elB.id}")
    hit("adminA_classeB", f"/classes/{cB.id}")
    hit("adminA_coursB", f"/cours/{coB.id}")
    hit("adminA_paiementB", f"/paiement/{payB.id}/recu")
    hit("adminA_noteB_edit", f"/note/{nB.id}/modifier")
    hit("adminA_absenceB_edit", f"/absences/edit/{abB.id}")

    login(pUser)
    hit("profA_classeA", f"/classes/{cA.id}")
    hit("profA_classeA2", f"/classes/{cA2.id}")
    hit("profA_api_A2", f"/api/eleves/classe/{cA2.id}")
    hit("profA_sync_eleveB", "/api/sync", "POST", [{"type": "note", "eleve_id": elB.id, "cours_id": coB.id, "valeur": 10, "date_evaluation": "2090-01-01 00:00:00"}])

    login(parentA)
    hit("parentA_childA1", f"/voir_eleve/{elA1.id}")
    hit("parentA_childA2", f"/voir_eleve/{elA2.id}")
    hit("parentA_childB", f"/voir_eleve/{elB.id}")
    hit("parentA_payB", f"/paiement/{payB.id}/recu")
finally:
    db.session.rollback()
    ids = [row[0] for row in db.session.execute(db.text("SELECT id FROM ecole WHERE nom LIKE :prefix"), {"prefix": mark + "%"}).all()]
    if ids:
        id_csv = ",".join(str(i) for i in ids)
        for sql in [
            f"DELETE FROM professeur_classes WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM note WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM absence WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM paiement WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM inscriptions WHERE classe_id IN (SELECT id FROM classe WHERE ecole_id IN ({id_csv})) OR eleve_id IN (SELECT id FROM eleve WHERE ecole_id IN ({id_csv}))",
            f"DELETE FROM cours WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM eleve WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM classe WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM annee_scolaire WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM professeur WHERE ecole_id IN ({id_csv})",
            f"DELETE FROM utilisateur WHERE ecole_id IN ({id_csv}) OR email LIKE '{mark}%'",
            f"DELETE FROM ecole WHERE id IN ({id_csv})",
        ]:
            db.session.execute(db.text(sql))
        db.session.commit()
    ctx.pop()
