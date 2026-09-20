from datetime import date
from pathlib import Path

from app import create_app, db
from app.models import Absence, AnneeScolaire, Classe, Cours, Ecole, Eleve, Inscription, Note, Utilisateur


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-option-b-offline"
    LOGIN_DISABLED = False


class CsrfEnabledConfig(TestConfig):
    WTF_CSRF_ENABLED = True
    WTF_CSRF_CHECK_DEFAULT = True


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["ecole_id"] = user.ecole_id


def _build_context():
    ecole_a = Ecole(nom="Ecole A", statut="active", onboarding_complete=True)
    ecole_b = Ecole(nom="Ecole B", statut="active", onboarding_complete=True)
    db.session.add_all([ecole_a, ecole_b])
    db.session.flush()

    annee = AnneeScolaire(
        nom="2026-2027",
        date_debut=date(2026, 9, 1),
        date_fin=date(2027, 6, 30),
        statut="active",
        ecole_id=ecole_a.id,
    )
    db.session.add(annee)
    db.session.flush()

    classe = Classe(nom="6e A", niveau="6e", ecole_id=ecole_a.id, annee_scolaire_id=annee.id)
    db.session.add(classe)
    db.session.flush()

    eleve = Eleve(nom="Test", prenom="Eleve", date_naissance=date(2014, 1, 1), ecole_id=ecole_a.id)
    db.session.add(eleve)
    db.session.flush()

    inscription = Inscription(eleve_id=eleve.id, classe_id=classe.id, annee_scolaire_id=annee.id, ecole_id=ecole_a.id)
    cours = Cours(nom="Math", coefficient=1, ecole_id=ecole_a.id, classe_id=classe.id)
    admin_a = Utilisateur(nom="Admin A", email="admin-a@test.local", mot_de_passe="x", role="admin", ecole_id=ecole_a.id)
    admin_b = Utilisateur(nom="Admin B", email="admin-b@test.local", mot_de_passe="x", role="admin", ecole_id=ecole_b.id)
    db.session.add_all([inscription, cours, admin_a, admin_b])
    db.session.commit()
    return ecole_a, ecole_b, annee, classe, eleve, cours, admin_a, admin_b


def test_service_worker_option_b_no_private_html_runtime_cache():
    sw = Path("app/static/service-worker.js").read_text(encoding="utf-8")

    assert "klasora-static-v11" in sw
    assert "klasora-pages-v10" not in sw
    assert "cacheName.startsWith('klasora-pages-')" in sw
    assert "await cache.put(request" not in sw
    assert "pageCache.match" not in sw
    assert "OFFLINE_URL" in sw
    assert "url.pathname.startsWith('/static/')" in sw


def test_offline_manager_refreshes_csrf_before_sync():
    js = Path("app/static/js/offline-manager.js").read_text(encoding="utf-8")

    assert "async getFreshCsrfToken()" in js
    assert "fetch('/api/csrf-token'" in js
    assert "await this.getFreshCsrfToken()" in js
    assert "document.querySelector('meta[name=\"csrf-token\"]').getAttribute('content')" not in js


def test_offline_forms_option_b_notes_absences_only():
    js = Path("app/static/js/offline-forms.js").read_text(encoding="utf-8")

    assert "setupNotesOffline();" in js
    assert "setupAbsencesOffline();" in js
    assert "setupElevesOffline();" not in js
    assert "'/cours'" in js
    assert "'/paiements'" in js
    assert "Connexion Internet requise pour cr" in js


def test_api_sync_rejects_other_user_other_school_and_online_only_types():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        ecole_a, ecole_b, _annee, _classe, eleve, cours, admin_a, admin_b = _build_context()
        client = app.test_client()
        _login(client, admin_a)

        other_user = client.post("/api/sync", json=[{
            "type": "note",
            "client_op_id": "op-other-user",
            "user_id": admin_b.id,
            "ecole_id": ecole_a.id,
            "eleve_id": eleve.id,
            "cours_id": cours.id,
            "valeur": 12,
            "date_evaluation": "2026-10-01",
        }])
        assert other_user.status_code == 200
        assert other_user.get_json()["results"][0]["status"] == "forbidden"

        other_school = client.post("/api/sync", json=[{
            "type": "note",
            "client_op_id": "op-other-school",
            "user_id": admin_a.id,
            "ecole_id": ecole_b.id,
            "eleve_id": eleve.id,
            "cours_id": cours.id,
            "valeur": 12,
            "date_evaluation": "2026-10-01",
        }])
        assert other_school.status_code == 200
        assert other_school.get_json()["results"][0]["status"] == "forbidden"

        online_only = client.post("/api/sync", json=[{
            "type": "eleve_creation",
            "client_op_id": "op-eleve-online-only",
            "user_id": admin_a.id,
            "ecole_id": ecole_a.id,
            "nom": "Offline",
            "prenom": "Blocked",
        }])
        assert online_only.status_code == 200
        assert online_only.get_json()["results"][0]["status"] == "forbidden"

        db.session.remove()
        db.drop_all()


def test_api_sync_note_idempotence_and_absence_success():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        ecole_a, _ecole_b, _annee, _classe, eleve, cours, admin_a, _admin_b = _build_context()
        client = app.test_client()
        _login(client, admin_a)

        note_payload = {
            "type": "note",
            "client_op_id": "op-note-1",
            "user_id": admin_a.id,
            "ecole_id": ecole_a.id,
            "eleve_id": eleve.id,
            "cours_id": cours.id,
            "valeur": 14,
            "date_evaluation": "2026-10-02",
            "type_evaluation": "Devoir",
        }
        first = client.post("/api/sync", json=[note_payload])
        second = client.post("/api/sync", json=[note_payload])

        assert first.status_code == 200
        assert first.get_json()["results"][0]["status"] == "synced"
        assert second.status_code == 200
        assert second.get_json()["results"][0]["status"] == "already_processed"
        assert Note.query.filter_by(eleve_id=eleve.id, cours_id=cours.id).count() == 1

        absence = client.post("/api/sync", json=[{
            "type": "absence",
            "client_op_id": "op-absence-1",
            "user_id": admin_a.id,
            "ecole_id": ecole_a.id,
            "eleve_id": eleve.id,
            "cours_id": cours.id,
            "date_absence": "2026-10-03",
            "motif": "Maladie",
        }])

        assert absence.status_code == 200
        assert absence.get_json()["results"][0]["status"] == "synced"
        assert Absence.query.filter_by(eleve_id=eleve.id, cours_id=cours.id).count() == 1

        db.session.remove()
        db.drop_all()


def test_api_sync_requires_csrf_when_csrf_enabled():
    app = create_app(CsrfEnabledConfig)
    with app.app_context():
        db.create_all()
        _ecole_a, _ecole_b, _annee, _classe, _eleve, _cours, admin_a, _admin_b = _build_context()
        client = app.test_client()
        _login(client, admin_a)

        response = client.post("/api/sync", json=[{
            "type": "test",
            "client_op_id": "op-csrf-required",
            "user_id": admin_a.id,
            "ecole_id": admin_a.ecole_id,
        }])

        assert response.status_code in (400, 403)

        db.session.remove()
        db.drop_all()


def test_api_sync_accepts_fresh_csrf_token_when_csrf_enabled():
    app = create_app(CsrfEnabledConfig)
    with app.app_context():
        db.create_all()
        _ecole_a, _ecole_b, _annee, _classe, _eleve, _cours, admin_a, _admin_b = _build_context()
        client = app.test_client()
        _login(client, admin_a)

        token_response = client.get("/api/csrf-token")
        assert token_response.status_code == 200
        token = token_response.get_json()["csrf_token"]

        response = client.post(
            "/api/sync",
            json=[{
                "type": "test",
                "client_op_id": "op-csrf-fresh",
                "user_id": admin_a.id,
                "ecole_id": admin_a.ecole_id,
            }],
            headers={"X-CSRFToken": token},
        )

        assert response.status_code == 200
        assert response.get_json()["results"][0]["status"] == "synced"

        db.session.remove()
        db.drop_all()
