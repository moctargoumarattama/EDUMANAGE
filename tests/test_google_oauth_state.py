import unittest
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from flask_login import login_user

from app import create_app, db
from app.models import Ecole, Utilisateur
from app.routes.email_settings import (
    GoogleOAuthStateError,
    generate_google_oauth_state,
    google_mail_callback,
    validate_google_oauth_state,
)


class OAuthStateTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "oauth-state-test-secret"
    LOGIN_DISABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    USE_PROXY_FIX = False


class GoogleOAuthStateTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(OAuthStateTestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.ecole = Ecole(nom="OAuth School")
        db.session.add(self.ecole)
        db.session.commit()
        self.admin = Utilisateur(
            nom="Admin",
            email="oauth-admin@test.local",
            mot_de_passe="x",
            role="admin",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_state_valide_accepte(self):
        state = generate_google_oauth_state(self.admin.id, self.ecole.id)
        payload = validate_google_oauth_state(state, self.admin.id, self.ecole.id)
        self.assertEqual(payload["user_id"], self.admin.id)
        self.assertEqual(payload["ecole_id"], self.ecole.id)

    def test_state_modifie_refuse(self):
        state = generate_google_oauth_state(self.admin.id, self.ecole.id) + "x"
        with self.assertRaises(GoogleOAuthStateError):
            validate_google_oauth_state(state, self.admin.id, self.ecole.id)

    def test_state_expire_refuse(self):
        state = generate_google_oauth_state(self.admin.id, self.ecole.id)
        with self.assertRaises(GoogleOAuthStateError):
            validate_google_oauth_state(state, self.admin.id, self.ecole.id, max_age=-1)

    def test_mauvais_user_id_refuse(self):
        state = generate_google_oauth_state(self.admin.id, self.ecole.id)
        with self.assertRaises(GoogleOAuthStateError):
            validate_google_oauth_state(state, self.admin.id + 1, self.ecole.id)

    def test_mauvais_ecole_id_refuse(self):
        state = generate_google_oauth_state(self.admin.id, self.ecole.id)
        with self.assertRaises(GoogleOAuthStateError):
            validate_google_oauth_state(state, self.admin.id, self.ecole.id + 1)

    def test_callback_sans_state_refuse(self):
        with self.app.test_request_context("/google/mail/callback?code=abc"):
            login_user(self.admin)
            response = google_mail_callback()
            self.assertEqual(response.status_code, 302)

    def test_callback_fonctionne_sans_state_en_session(self):
        state = generate_google_oauth_state(self.admin.id, self.ecole.id)
        url = f"/google/mail/callback?state={state}&code=abc"
        with self.app.test_request_context(url):
            login_user(self.admin)
            with patch("app.routes.email_settings.connect_school_gmail", return_value="school@gmail.com") as connect:
                response = google_mail_callback()
        self.assertEqual(response.status_code, 302)
        connect.assert_called_once_with(self.ecole.id, "abc")


if __name__ == "__main__":
    unittest.main()
