import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import Ecole, Eleve, Utilisateur


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class AuthTelephoneParentTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(
            nom="Ecole Niger Test",
            telephone="90000000",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        self.admin = Utilisateur(
            nom="Admin",
            email="admin.auth@test.local",
            role="admin",
            telephone="90111111",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        self.admin.set_mot_de_passe("AdminPass123")

        self.parent = Utilisateur(
            nom="Parent",
            email="parent.auth@test.local",
            role="parent",
            telephone="+227 90 12 34 56",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        self.parent.set_mot_de_passe("1234")
        self.parent_maroc = Utilisateur(
            nom="Parent Maroc",
            email=None,
            role="parent",
            telephone="+212 6 12 34 56 78",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        self.parent_maroc.set_mot_de_passe("5678")
        db.session.add_all([self.admin, self.parent, self.parent_maroc])
        db.session.flush()

        self.eleve = Eleve(
            nom="Eleve",
            prenom="Test",
            date_naissance=date(2016, 1, 1),
            genre="M",
            contact_parent="90-12-34-56",
            parent_id=self.parent.id,
            ecole_id=self.ecole.id,
        )
        db.session.add(self.eleve)
        self.eleve_maroc = Eleve(
            nom="Eleve Maroc",
            prenom="Test",
            date_naissance=date(2016, 2, 1),
            genre="F",
            contact_parent="+212612345678",
            parent_id=self.parent_maroc.id,
            ecole_id=self.ecole.id,
        )
        db.session.add(self.eleve_maroc)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def post_login(self, identifiant, password, login_type="terrain"):
        data = {"login_type": login_type, "password": password}
        if login_type == "admin":
            data["email"] = identifiant
        else:
            data["identifiant"] = identifiant
            data["telephone"] = identifiant
            data["mot_de_passe"] = password
        return self.app.test_client().post(
            "/login",
            data=data,
        )

    def test_admin_login_email_password(self):
        response = self.post_login("ADMIN.AUTH@test.local", "AdminPass123", login_type="admin")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.headers["Location"])

    def test_admin_login_phone_password_refused_on_terrain_space(self):
        response = self.post_login("90111111", "AdminPass123")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Direction", response.data)

    def test_parent_login_local_phone_pin(self):
        response = self.post_login("90123456", "1234")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/parent", response.headers["Location"])

    def test_parent_login_formatted_phone_pin(self):
        for identifiant in ("+227 90 12 34 56", "90-12-34-56"):
            response = self.post_login(identifiant, "1234")
            self.assertEqual(response.status_code, 302)
            self.assertIn("/parent", response.headers["Location"])

    def test_parent_login_international_phone_pin(self):
        response = self.post_login("+212 6 12 34 56 78", "5678")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/parent", response.headers["Location"])

    def test_parent_login_morocco_local_phone_pin(self):
        response = self.post_login("0612345678", "5678")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/parent", response.headers["Location"])

    def test_bad_phone_or_pin_fails_without_crash(self):
        response = self.post_login("90123456", "9999")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Identifiant ou mot de passe / code PIN incorrect.",
            response.get_data(as_text=True),
        )

        response = self.post_login("70000000", "1234")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Identifiant ou mot de passe / code PIN incorrect.",
            response.get_data(as_text=True),
        )


if __name__ == "__main__":
    unittest.main()
