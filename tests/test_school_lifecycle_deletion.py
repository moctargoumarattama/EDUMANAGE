import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.config import Config
from app.models import Ecole, Utilisateur
from app.services.school_lifecycle import (
    SCHOOL_DELETE_CONFIRMATION_PHRASE,
    days_until_school_deletion,
    is_school_deletion_eligible,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class SchoolLifecycleDeletionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.super_admin = Utilisateur(
            nom="Super",
            email="super@example.com",
            role="super_admin",
            statut="actif",
        )
        self.super_admin.set_mot_de_passe("secret")
        self.ecole = Ecole(nom="Ecole Test", statut="actif", onboarding_complete=True)
        db.session.add_all([self.super_admin, self.ecole])
        db.session.flush()
        self.admin = Utilisateur(
            nom="Admin",
            email="admin@example.com",
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        self.admin.set_mot_de_passe("secret")
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def client_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            if user.ecole_id:
                session["ecole_id"] = user.ecole_id
        return client

    def post_delete(self, client, ecole=None, nom=None, phrase=None):
        ecole = ecole or self.ecole
        return client.post(
            f"/admin/ecoles/{ecole.id}/supprimer",
            data={
                "confirmation_nom": ecole.nom if nom is None else nom,
                "confirmation_phrase": SCHOOL_DELETE_CONFIRMATION_PHRASE if phrase is None else phrase,
            },
        )

    def test_active_school_permanent_delete_refused(self):
        client = self.client_as(self.super_admin)
        response = self.post_delete(client)
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Ecole.query.get(self.ecole.id))

    def test_disabled_school_before_30_days_refused(self):
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow() - timedelta(days=5)
        db.session.commit()

        self.assertFalse(is_school_deletion_eligible(self.ecole))
        self.assertGreater(days_until_school_deletion(self.ecole), 0)

        client = self.client_as(self.super_admin)
        self.post_delete(client)
        self.assertIsNotNone(Ecole.query.get(self.ecole.id))

    def test_disabled_school_29_days_23h_refused(self):
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow() - timedelta(days=29, hours=23)
        db.session.commit()

        client = self.client_as(self.super_admin)
        self.post_delete(client)
        self.assertIsNotNone(Ecole.query.get(self.ecole.id))

    def test_disabled_school_30_days_eligible(self):
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow() - timedelta(days=30)
        db.session.commit()

        self.assertTrue(is_school_deletion_eligible(self.ecole))

    def test_reactivation_resets_counter_and_new_disable_restarts_delay(self):
        client = self.client_as(self.super_admin)
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow() - timedelta(days=31)
        db.session.commit()

        client.post(f"/admin/ecoles/{self.ecole.id}/bloquer", data={"action": "debloquer"})
        db.session.refresh(self.ecole)
        self.assertEqual(self.ecole.statut, "actif")
        self.assertIsNone(self.ecole.disabled_at)
        self.assertFalse(is_school_deletion_eligible(self.ecole))

        client.post(f"/admin/ecoles/{self.ecole.id}/bloquer", data={"action": "bloquer"})
        db.session.refresh(self.ecole)
        self.assertEqual(self.ecole.statut, "bloque")
        self.assertIsNotNone(self.ecole.disabled_at)
        self.assertFalse(is_school_deletion_eligible(self.ecole))

    def test_disabled_school_user_login_and_active_session_blocked(self):
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow()
        db.session.commit()

        login_response = self.app.test_client().post(
            "/login",
            data={"email": self.admin.email, "mot_de_passe": "secret"},
        )
        self.assertEqual(login_response.status_code, 302)

        client = self.client_as(self.admin)
        response = client.get("/eleves")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_super_admin_still_accesses_admin_schools(self):
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow()
        db.session.commit()

        client = self.client_as(self.super_admin)
        response = client.get("/admin/ecoles")
        self.assertEqual(response.status_code, 200)

    def test_get_delete_route_405(self):
        client = self.client_as(self.super_admin)
        response = client.get(f"/admin/ecoles/{self.ecole.id}/supprimer")
        self.assertEqual(response.status_code, 405)

    def test_wrong_confirmation_refused(self):
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow() - timedelta(days=31)
        db.session.commit()

        client = self.client_as(self.super_admin)
        self.post_delete(client, nom="Autre ecole")
        self.assertIsNotNone(Ecole.query.get(self.ecole.id))

        self.post_delete(client, phrase="SUPPRIMER")
        self.assertIsNotNone(Ecole.query.get(self.ecole.id))

    def test_valid_permanent_delete_after_30_days(self):
        self.ecole.statut = "inactive"
        self.ecole.disabled_at = datetime.utcnow() - timedelta(days=31)
        db.session.commit()
        ecole_id = self.ecole.id

        client = self.client_as(self.super_admin)
        response = self.post_delete(client)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Ecole.query.get(ecole_id))


if __name__ == "__main__":
    unittest.main()
