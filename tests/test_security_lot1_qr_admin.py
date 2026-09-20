from datetime import date
from unittest.mock import patch

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Utilisateur
from app.services.bulletin_verification import generer_token_eleve


class BaseTestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-security-lot1"
    WTF_CSRF_ENABLED = False
    LOGIN_DISABLED = False


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if user.ecole_id:
            sess["ecole_id"] = user.ecole_id


def _build_school(name):
    ecole = Ecole(nom=name, statut="active")
    ecole.onboarding_complete = True
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
    eleve = Eleve(nom=f"{name} Eleve", prenom="Test", date_naissance=date(2014, 1, 1), ecole_id=ecole.id)
    db.session.add(eleve)
    db.session.flush()
    inscription = Inscription(eleve_id=eleve.id, classe_id=classe.id, annee_scolaire_id=annee.id, ecole_id=ecole.id)
    db.session.add(inscription)
    db.session.flush()
    return ecole, annee, classe, eleve, inscription


class TestSecurityLot1QrAdmin:
    def setup_method(self):
        self.app = create_app(BaseTestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.ecole_a, self.annee_a, self.classe_a, self.eleve_a, self.ins_a = _build_school("A")
        self.ecole_b, self.annee_b, self.classe_b, self.eleve_b, self.ins_b = _build_school("B")

        self.admin_a = Utilisateur(nom="Admin A", email="admin-a@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Admin B", email="admin-b@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_b.id)
        self.super_admin = Utilisateur(nom="Super", email="super@test.local", mot_de_passe="x", role="super_admin")
        db.session.add_all([self.admin_a, self.admin_b, self.super_admin])
        db.session.commit()

    def teardown_method(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_qr_info_by_id_requires_authentication_and_does_not_expose_internal_id(self):
        anonymous = self.client.get(f"/api/qr/info/{self.eleve_a.id}")
        assert anonymous.status_code in (302, 401)

        _login(self.client, self.admin_a)
        response = self.client.get(f"/api/qr/info/{self.eleve_a.id}")
        assert response.status_code == 200
        assert response.headers["Cache-Control"].startswith("no-store")
        data = response.get_json()
        assert data["classe"] == "6e A"
        assert "eleve_id" not in data
        assert "email" not in data
        assert "telephone" not in data

    def test_qr_info_by_id_refuses_other_school(self):
        _login(self.client, self.admin_a)
        response = self.client.get(f"/api/qr/info/{self.eleve_b.id}")
        assert response.status_code == 403

    def test_public_student_qr_uses_token_and_no_store(self):
        token = generer_token_eleve(self.ecole_a.id, self.ins_a.id)
        response = self.client.get(f"/verifier/eleve/{token}")
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert response.headers["Cache-Control"].startswith("no-store")
        assert "noindex" in response.headers["X-Robots-Tag"]
        assert "eleve_id" not in body

    def test_admin_mutating_routes_reject_get(self):
        _login(self.client, self.super_admin)
        routes = [
            "/admin/backup",
            "/admin/clean",
            "/admin/delete_backup/test.zip",
            "/admin/create_tables",
            "/admin/init_annees",
            "/admin/backup_complete",
        ]
        for route in routes:
            response = self.client.get(route)
            assert response.status_code == 405

    def test_admin_mutating_routes_require_super_admin_and_post_works_for_super_admin(self):
        _login(self.client, self.admin_a)
        forbidden = self.client.post("/admin/clean")
        assert forbidden.status_code in (302, 403)

        _login(self.client, self.super_admin)
        with patch("app.admin.routes.clean_data", return_value="ok"):
            allowed = self.client.post("/admin/clean")
        assert allowed.status_code in (302, 303)


class CsrfEnabledConfig(BaseTestConfig):
    WTF_CSRF_ENABLED = True
    WTF_CSRF_CHECK_DEFAULT = True


class TestSecurityLot1Csrf:
    def setup_method(self):
        self.app = create_app(CsrfEnabledConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.super_admin = Utilisateur(nom="Super", email="super-csrf@test.local", mot_de_passe="x", role="super_admin")
        db.session.add(self.super_admin)
        db.session.commit()

    def teardown_method(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_admin_post_without_csrf_is_rejected_when_csrf_enabled(self):
        _login(self.client, self.super_admin)
        response = self.client.post("/admin/clean")
        assert response.status_code in (302, 400)
