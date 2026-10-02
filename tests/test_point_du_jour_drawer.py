import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Ecole, Utilisateur, AnneeScolaire


class PointDuJourTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-point-du-jour-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestPointDuJourDrawer(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PointDuJourTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        self.ecole = Ecole(
            nom="École Test Point du Jour",
            adresse="Niamey",
            telephone="90000000",
            email="direction@ecole.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)

        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Moussa",
            email="admin_pdj@ecole.ne",
            telephone="+22790000001",
            mot_de_passe=generate_password_hash("AdminPass123!"),
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.admin)
        db.session.commit()

        # Login admin via session
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['ecole_id'] = self.ecole.id
            sess['_fresh'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_point_du_jour_direct_url_redirects_to_dashboard(self):
        """La route /point-du-jour en accès direct redirige (302) vers le dashboard avec ouverture automatique du tiroir."""
        response = self.client.get("/point-du-jour", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("open_point_du_jour=1", response.headers.get("Location", ""))

    def test_point_du_jour_fragment_ajax(self):
        """La route /point-du-jour?fragment=1 retourne uniquement le fragment sans layout HTML complet."""
        response = self.client.get("/point-du-jour?fragment=1")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("point-du-jour-container", html)
        self.assertNotIn("<!DOCTYPE html>", html)
        self.assertNotIn("<body", html)

    def test_point_du_jour_drawer_in_base_template(self):
        """Le template de base inclut le composant offcanvas et la languette de déclenchement pour l'admin."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("offcanvasPointDuJour", html)
        self.assertIn("point-du-jour-drawer", html)
        self.assertIn("point-du-jour-tab-trigger", html)
        self.assertIn("point_du_jour_drawer.js", html)
