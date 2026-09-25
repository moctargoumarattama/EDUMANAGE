import unittest
from datetime import datetime
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Ecole, Utilisateur, Professeur, AnneeScolaire, EcoleGoogleMailConfig


class DashboardEmailAlertTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-secret-key-dashboard-email-alert"
    LOGIN_DISABLED = False
    SERVER_NAME = None


class TestDashboardEmailAlert(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DashboardEmailAlertTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # 1. Création de l'école
        self.ecole = Ecole(
            nom="Lycée de Test",
            email="contact@lycee-test.edu",
            onboarding_complete=True
        )
        db.session.add(self.ecole)
        db.session.commit()

        # 2. Création de l'année scolaire active
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=datetime(2025, 9, 1),
            date_fin=datetime(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        # 3. Administrateur
        self.admin = Utilisateur(
            nom="Admin",
            prenom="Directeur",
            email="admin@lycee-test.edu",
            role="admin",
            ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("password123")
        )
        db.session.add(self.admin)
        db.session.commit()

        # 4. Professeur
        self.prof_user = Utilisateur(
            nom="Prof",
            prenom="Jean",
            email="prof@lycee-test.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            mot_de_passe=generate_password_hash("password123")
        )
        db.session.add(self.prof_user)
        db.session.commit()

        self.professeur = Professeur(
            nom="Prof",
            prenom="Jean",
            email="prof@lycee-test.edu",
            code_prof="PROF_TEST_01",
            ecole_id=self.ecole.id,
            utilisateur_id=self.prof_user.id
        )
        db.session.add(self.professeur)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_bureau_affiche_alerte_email_non_connecte(self):
        """Vérifie que le bureau admin affiche la bannière d'alerte permanente si Gmail n'est pas connecté."""
        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['_fresh'] = True
                sess['ecole_id'] = self.ecole.id

            res = self.client.get("/")
            self.assertEqual(res.status_code, 200)
            content = res.get_data(as_text=True)

            # Doit afficher le titre d'alerte et le bouton d'action vers parametres/email
            self.assertIn("Messagerie officielle non configurée", content)
            self.assertIn("Connecter Gmail", content)
            self.assertIn("/parametres/email", content)

    def test_bureau_masque_alerte_email_si_connecte(self):
        """Vérifie que la bannière d'alerte disparaît du bureau dès que Gmail est connecté."""
        gmail_config = EcoleGoogleMailConfig(
            ecole_id=self.ecole.id,
            google_email="admin-gmail@lycee-test.edu",
            is_connected=True,
            connected_at=datetime.utcnow()
        )
        db.session.add(gmail_config)
        db.session.commit()

        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(self.admin.id)
                sess['_fresh'] = True
                sess['ecole_id'] = self.ecole.id

            res = self.client.get("/")
            self.assertEqual(res.status_code, 200)
            content = res.get_data(as_text=True)

            # Ne doit PLUS afficher la bannière d'alerte
            self.assertNotIn("Messagerie officielle de l'établissement non configurée", content)

    def test_bureau_professeur_ne_voit_pas_alerte_admin(self):
        """Vérifie que les professeurs n'ont pas cette alerte d'administration sur leur bureau."""
        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(self.prof_user.id)
                sess['_fresh'] = True
                sess['ecole_id'] = self.ecole.id

            res = self.client.get("/dashboard")
            self.assertEqual(res.status_code, 302)  # Redirige vers main.professeur_dashboard
            
            res_prof = self.client.get("/professeur/dashboard")
            self.assertEqual(res_prof.status_code, 200)
            content = res_prof.get_data(as_text=True)

            self.assertNotIn("Messagerie officielle de l'établissement non configurée", content)


if __name__ == '__main__':
    unittest.main()
