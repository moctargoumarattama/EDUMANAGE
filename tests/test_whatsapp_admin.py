import unittest
from unittest.mock import patch

import requests

from app import create_app, db
from app.config import Config
from app.models import Ecole, Utilisateur


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class FakeGatewayResponse:
    def __init__(self, payload, status_error=None):
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        return self.payload


class WhatsAppAdminTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole WhatsApp Admin", statut="actif", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.flush()

        self.admin = Utilisateur(
            nom="Admin",
            email="wa-admin@test.local",
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        self.admin.set_mot_de_passe("secret")
        db.session.add(self.admin)
        db.session.commit()

        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_status_returns_gateway_qr_payload(self):
        qr = "data:image/png;base64,abcd"
        with patch(
            "app.routes.whatsapp.requests.get",
            return_value=FakeGatewayResponse({"status": "ATTENTE_SCAN", "qr": qr}),
        ) as get_mock:
            response = self.client.get("/admin/whatsapp/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ATTENTE_SCAN", "qr": qr})
        get_mock.assert_called_once()
        self.assertEqual(get_mock.call_args.kwargs["timeout"], 2)

    def test_status_returns_indisponible_when_gateway_fails(self):
        with patch("app.routes.whatsapp.requests.get", side_effect=requests.exceptions.ConnectionError("offline")):
            response = self.client.get("/admin/whatsapp/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "INDISPONIBLE"})

    def test_status_filters_unexpected_qr_value(self):
        with patch(
            "app.routes.whatsapp.requests.get",
            return_value=FakeGatewayResponse({"status": "ATTENTE_SCAN", "qr": "javascript:alert(1)"}),
        ):
            response = self.client.get("/admin/whatsapp/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ATTENTE_SCAN"})

    def test_page_displays_connected_status(self):
        with patch(
            "app.routes.whatsapp.requests.get",
            return_value=FakeGatewayResponse({"status": "CONNECTE"}),
        ):
            response = self.client.get("/admin/whatsapp")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Numero officiel connecte", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
