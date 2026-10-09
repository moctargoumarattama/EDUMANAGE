"""tests/test_sync_et_hors_ligne_securise.py
=================================================
Tests pour le CHANTIER 2 :
1. Verrouillage strict de /api/sync contre les notes d'inscriptions radiées/annulées.
2. Unicité stricte de la composition lors de la synchronisation (mise à jour ou conflit, 0 doublon).
3. Validation du calendrier et des périodes (rejet de "Trimestre 99" et dates hors année).
4. Synchronisation d'un appel collectif de classe (type 'appel').
5. Isolation de la purge IndexedDB par utilisateur connecté (simulation logique).
"""

from datetime import date, datetime
import pytest
from app import create_app, db
from app.config import TestingConfig
from sqlalchemy.pool import StaticPool
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    Absence,
    PeriodeBulletin,
    SyncOperationLog,
    Utilisateur,
)


@pytest.fixture
def app_ctx():
    class SyncTestConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_ENGINE_OPTIONS = {
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        }
        SERVER_NAME = "localhost"

    app = create_app(SyncTestConfig)
    with app.app_context():
        assert str(db.engine.url) == "sqlite:///:memory:"
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["user_id"] = user.id
        sess["ecole_id"] = user.ecole_id
        sess["annee_consultee_id"] = None


def _setup_base_data():
    ecole = Ecole(nom="École Test Niamey", statut="active", onboarding_complete=True)
    db.session.add(ecole)
    db.session.flush()

    annee = AnneeScolaire(
        nom="2026-2027",
        date_debut=date(2026, 9, 1),
        date_fin=date(2027, 6, 30),
        statut="active",
        ecole_id=ecole.id,
    )
    db.session.add(annee)
    db.session.flush()

    classe = Classe(nom="6e A", niveau="6e", ecole_id=ecole.id, annee_scolaire_id=annee.id)
    db.session.add(classe)
    db.session.flush()

    cours = Cours(nom="Mathématiques", coefficient=2, ecole_id=ecole.id, classe_id=classe.id)
    db.session.add(cours)
    db.session.flush()

    admin = Utilisateur(
        nom="Admin",
        prenom="Principal",
        email="admin@test.local",
        mot_de_passe="secret",
        role="admin",
        ecole_id=ecole.id,
    )
    db.session.add(admin)
    db.session.flush()

    eleve = Eleve(
        nom="Moussa",
        prenom="Ibrahim",
        date_naissance=date(2013, 5, 10),
        ecole_id=ecole.id,
        statut="actif",
    )
    db.session.add(eleve)
    db.session.flush()

    inscription = Inscription(
        eleve_id=eleve.id,
        classe_id=classe.id,
        annee_scolaire_id=annee.id,
        ecole_id=ecole.id,
        statut="inscrit",
    )
    db.session.add(inscription)
    db.session.commit()

    return ecole, annee, classe, cours, admin, eleve, inscription


def test_sync_rejet_inscription_radiee_ou_annulee(app_ctx):
    """1. Contrôle du statut de scolarisation : rejet de la note si inscription non active."""
    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)

    # Cas A: Inscription avec statut 'radie'
    inscription.statut = "radie"
    db.session.commit()

    payload = [{
        "type": "note",
        "client_op_id": "op-note-radie-1",
        "eleve_id": eleve.id,
        "cours_id": cours.id,
        "valeur": 15,
        "date_evaluation": "2026-10-15",
        "periode": "Semestre 1",
        "type_evaluation": "Devoir",
        "coefficient": 1.0,
    }]

    res = client.post("/api/sync", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    item_res = data["results"][0]
    assert item_res["status"] == "conflict"
    assert item_res["reason"] == "inactive_enrollment"
    assert "Inscription non active" in item_res["message"]
    assert Note.query.filter_by(eleve_id=eleve.id).count() == 0

    # Cas B: Inscription avec statut 'annule'
    inscription.statut = "annule"
    db.session.commit()

    payload[0]["client_op_id"] = "op-note-annule-1"
    res = client.post("/api/sync", json=payload)
    data = res.get_json()
    assert data["results"][0]["status"] == "conflict"
    assert "Inscription non active" in data["results"][0]["message"]
    assert Note.query.filter_by(eleve_id=eleve.id).count() == 0


def test_sync_rejet_periode_invalide_fantome(app_ctx):
    """3a. Validation période : rejet strict de périodes fantômes ('Trimestre 99')."""
    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)

    payload = [{
        "type": "note",
        "client_op_id": "op-note-fake-period",
        "eleve_id": eleve.id,
        "cours_id": cours.id,
        "valeur": 12,
        "date_evaluation": "2026-10-15",
        "periode": "Trimestre 99",
        "type_evaluation": "Devoir",
        "coefficient": 1.0,
    }]

    res = client.post("/api/sync", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    item_res = data["results"][0]
    assert item_res["status"] == "conflict"
    assert item_res["reason"] == "invalid_period"
    assert "Trimestre 99" in item_res["message"]
    assert Note.query.filter_by(eleve_id=eleve.id).count() == 0


def test_sync_rejet_date_hors_annee_scolaire(app_ctx):
    """3b. Validation calendrier : rejet si date_evaluation est hors de l'année scolaire active."""
    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)

    # Date antérieure au début (2026-09-01)
    payload_before = [{
        "type": "note",
        "client_op_id": "op-note-date-before",
        "eleve_id": eleve.id,
        "cours_id": cours.id,
        "valeur": 14,
        "date_evaluation": "2025-05-10",
        "periode": "Semestre 1",
        "type_evaluation": "Devoir",
    }]
    res_before = client.post("/api/sync", json=payload_before)
    item_before = res_before.get_json()["results"][0]
    assert item_before["status"] == "conflict"
    assert item_before["reason"] == "date_outside_school_year"

    # Date postérieure à la fin (2027-06-30)
    payload_after = [{
        "type": "note",
        "client_op_id": "op-note-date-after",
        "eleve_id": eleve.id,
        "cours_id": cours.id,
        "valeur": 14,
        "date_evaluation": "2028-01-10",
        "periode": "Semestre 1",
        "type_evaluation": "Devoir",
    }]
    res_after = client.post("/api/sync", json=payload_after)
    item_after = res_after.get_json()["results"][0]
    assert item_after["status"] == "conflict"
    assert item_after["reason"] == "date_outside_school_year"
    assert Note.query.filter_by(eleve_id=eleve.id).count() == 0


def test_sync_unicite_composition_mise_a_jour_sans_doublon(app_ctx):
    """2. Règle absolue d'unicité de la composition : mise à jour sans jamais créer de 2e Note."""
    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)

    # Création initiale d'une première composition
    first_payload = [{
        "type": "note",
        "client_op_id": "op-comp-1",
        "eleve_id": eleve.id,
        "cours_id": cours.id,
        "valeur": 11,
        "date_evaluation": "2026-11-20",
        "periode": "Semestre 1",
        "type_evaluation": "Composition",
        "coefficient": 2.0,
    }]
    res1 = client.post("/api/sync", json=first_payload)
    assert res1.get_json()["results"][0]["status"] == "synced"
    assert Note.query.filter_by(eleve_id=eleve.id, cours_id=cours.id).count() == 1
    comp1 = Note.query.filter_by(eleve_id=eleve.id, cours_id=cours.id).first()
    assert comp1.valeur == 11
    assert comp1.sync_version == 1

    # Tentative de synchro d'une 2e composition pour le même cours et la même période,
    # avec une date différente et une nouvelle valeur en se basant sur base_version=1
    second_payload = [{
        "type": "note",
        "client_op_id": "op-comp-2",
        "eleve_id": eleve.id,
        "cours_id": cours.id,
        "valeur": 16,
        "date_evaluation": "2026-11-25",
        "periode": "Semestre 1",
        "type_evaluation": "Composition",
        "coefficient": 2.0,
        "base_version": 1,
    }]
    res2 = client.post("/api/sync", json=second_payload)
    item_res2 = res2.get_json()["results"][0]
    assert item_res2["status"] == "synced"

    # Vérification stricte : toujours exactement 1 seule note de composition en base !
    assert Note.query.filter_by(eleve_id=eleve.id, cours_id=cours.id).count() == 1
    db.session.refresh(comp1)
    assert comp1.valeur == 16
    assert comp1.sync_version == 2

    # Tentative d'écrasement concurrent sans base_version -> conflit, aucun doublon créé
    third_payload = [{
        "type": "note",
        "client_op_id": "op-comp-3",
        "eleve_id": eleve.id,
        "cours_id": cours.id,
        "valeur": 8,
        "date_evaluation": "2026-11-28",
        "periode": "Semestre 1",
        "type_evaluation": "Composition",
        "coefficient": 2.0,
    }]
    res3 = client.post("/api/sync", json=third_payload)
    item_res3 = res3.get_json()["results"][0]
    assert item_res3["status"] == "conflict"
    assert Note.query.filter_by(eleve_id=eleve.id, cours_id=cours.id).count() == 1
    assert comp1.valeur == 16


def test_sync_appel_collectif(app_ctx):
    """4. Synchronisation d'un appel collectif de classe (type 'appel')."""
    ecole, annee, classe, cours, admin, eleve1, ins1 = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)

    # Ajouter un 2e élève
    eleve2 = Eleve(nom="Ali", prenom="Fatima", date_naissance=date(2013, 8, 12), ecole_id=ecole.id, statut="actif")
    db.session.add(eleve2)
    db.session.flush()
    ins2 = Inscription(eleve_id=eleve2.id, classe_id=classe.id, annee_scolaire_id=annee.id, ecole_id=ecole.id, statut="inscrit")
    db.session.add(ins2)
    db.session.commit()

    # Appel pris hors ligne : élève 1 est absent, élève 2 est présent
    appel_payload = [{
        "type": "appel",
        "client_op_id": "op-appel-collectif-1",
        "classe_id": classe.id,
        "cours_id": cours.id,
        "date_appel": "2026-10-10",
        "heure": "08:30",
        "professeur_id": admin.id,
        "absent_inscription_ids": [ins1.id],
    }]

    res = client.post("/api/sync", json=appel_payload)
    assert res.status_code == 200
    item_res = res.get_json()["results"][0]
    assert item_res["status"] == "synced"
    assert item_res["absents_count"] == 1

    # Vérifier l'absence créée
    absences = Absence.query.filter_by(date_absence=date(2026, 10, 10), cours_id=cours.id).all()
    assert len(absences) == 1
    assert absences[0].eleve_id == eleve1.id
    assert absences[0].inscription_id == ins1.id

    # Idempotence : renvoi du même appel -> already_processed
    res_dup = client.post("/api/sync", json=appel_payload)
    assert res_dup.get_json()["results"][0]["status"] == "already_processed"


def test_sync_refuse_type_et_periode_non_configures(app_ctx):
    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)
    note = {
        "type": "note", "client_op_id": "type-inconnu", "eleve_id": eleve.id,
        "cours_id": cours.id, "valeur": 12, "date_evaluation": "2026-10-15",
        "periode": "Semestre 1", "type_evaluation": "Type inventé",
    }
    assert client.post('/api/sync', json=[note]).get_json()['results'][0]['reason'] == 'invalid_evaluation_type'
    note.update(client_op_id='periode-non-configuree', type_evaluation='Devoir', periode='Trimestre 1')
    assert client.post('/api/sync', json=[note]).get_json()['results'][0]['reason'] == 'invalid_period'
    assert Note.query.count() == 0


def test_sync_composition_insensible_a_la_casse(app_ctx):
    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)
    note = {
        "type": "note", "client_op_id": "composition-casse-1", "eleve_id": eleve.id,
        "cours_id": cours.id, "valeur": 8, "date_evaluation": "2026-10-15",
        "periode": "Semestre 2", "type_evaluation": "Composition",
    }
    assert client.post('/api/sync', json=[note]).get_json()['results'][0]['status'] == 'synced'
    note.update(client_op_id='composition-casse-2', valeur=18,
                date_evaluation='2026-10-20', periode='semestre 2')
    resultat = client.post('/api/sync', json=[note]).get_json()['results'][0]
    assert resultat['status'] == 'conflict'
    assert Note.query.count() == 1
    assert Note.query.first().periode == 'Semestre 2'


def test_primaire_saisie_et_sync_acceptent_trois_compositions(app_ctx):
    from app.services.notes_annuelles import periodes_notes_classe, valider_mutation_note

    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    classe.nom = 'CM2 A'
    classe.niveau = 'CM2'
    db.session.commit()
    attendues = ['1ère Composition', '2ème Composition', '3ème Composition']
    assert periodes_notes_classe(classe, annee) == attendues
    for periode in attendues:
        valide, erreur, *_ = valider_mutation_note(
            ecole.id, annee, admin, eleve.id, cours.id, 12,
            type_evaluation='Composition', periode=periode,
        )
        assert valide, erreur

    client = app_ctx.test_client()
    _login(client, admin)
    note = {"type": "note", "client_op_id": "primaire-comp-3", "eleve_id": eleve.id,
            "cours_id": cours.id, "valeur": 12, "date_evaluation": "2026-10-15",
            "periode": "3ème Composition", "type_evaluation": "Composition"}
    assert client.post('/api/sync', json=[note]).get_json()['results'][0]['status'] == 'synced'
    assert Note.query.one().periode == '3ème Composition'
    Note.query.one().periode = '3ÈME COMPOSITION'
    db.session.commit()
    note.update(client_op_id='primaire-comp-3-bis', valeur=18, date_evaluation='2026-10-20')
    assert client.post('/api/sync', json=[note]).get_json()['results'][0]['status'] == 'conflict'
    assert Note.query.count() == 1


def test_sync_appel_ne_supprime_pas_une_absence_justifiee(app_ctx):
    ecole, annee, classe, cours, admin, eleve, inscription = _setup_base_data()
    client = app_ctx.test_client()
    _login(client, admin)
    absence = Absence(date_absence=date(2026, 10, 10), eleve_id=eleve.id,
                      cours_id=cours.id, ecole_id=ecole.id, inscription_id=inscription.id,
                      justifiee=True, last_by_admin=True, sync_version=5)
    db.session.add(absence)
    db.session.commit()
    appel = {"type": "appel", "client_op_id": "appel-protege", "classe_id": classe.id,
             "cours_id": cours.id, "date_appel": "2026-10-10", "heure": "08:30",
             "absent_inscription_ids": []}
    resultat = client.post('/api/sync', json=[appel]).get_json()['results'][0]
    assert resultat['status'] == 'conflict'
    assert db.session.get(Absence, absence.id).justifiee is True

    absence.justifiee = False
    absence.last_by_admin = False
    db.session.commit()
    appel['client_op_id'] = 'appel-non-versionne'
    resultat = client.post('/api/sync', json=[appel]).get_json()['results'][0]
    assert resultat['status'] == 'conflict'
    assert db.session.get(Absence, absence.id) is not None

    appel.update(client_op_id='appel-date-invalide', date_appel='1999-01-01')
    resultat = client.post('/api/sync', json=[appel]).get_json()['results'][0]
    assert resultat['status'] == 'conflict'
    assert Absence.query.count() == 1


def test_simulation_purge_indexeddb_isolee_par_utilisateur():
    """5. Test unitaire de la logique de viderFileUtilisateurCourant(userId, ecoleId) :
    Vérifie qu'un compte n'efface jamais les opérations d'un autre utilisateur."""
    # Simulation de la structure IndexedDB pendingSync
    mock_store = [
        {"id": 1, "user_id": 10, "ecole_id": 1, "type": "note", "valeur": 15},
        {"id": 2, "user_id": 10, "ecole_id": 1, "type": "note", "valeur": 12},
        {"id": 3, "user_id": 20, "ecole_id": 1, "type": "note", "valeur": 18},  # Autre utilisateur !
        {"id": 4, "user_id": 10, "ecole_id": 2, "type": "absence"},             # Autre école
    ]

    def viderFileUtilisateurCourant(store, target_user_id, target_ecole_id):
        nonlocal mock_store
        deleted = 0
        restant = []
        for item in store:
            match_user = (item.get("user_id") == target_user_id)
            match_ecole = not target_ecole_id or (item.get("ecole_id") == target_ecole_id)
            if match_user and match_ecole:
                deleted += 1
            else:
                restant.append(item)
        mock_store = restant
        return deleted

    # Purge par l'utilisateur 10 pour l'école 1
    nb_deleted = viderFileUtilisateurCourant(mock_store, target_user_id=10, target_ecole_id=1)
    assert nb_deleted == 2

    # L'utilisateur 20 conserve intactes ses opérations en attente !
    user20_items = [it for it in mock_store if it["user_id"] == 20]
    assert len(user20_items) == 1
    assert user20_items[0]["valeur"] == 18

    # L'opération de l'école 2 est également conservée
    ecole2_items = [it for it in mock_store if it["ecole_id"] == 2]
    assert len(ecole2_items) == 1

