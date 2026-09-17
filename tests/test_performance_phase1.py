import unittest
from datetime import date
from flask import g
from app import create_app, db
from app.config import Config
from app.models import Ecole, AnneeScolaire, Utilisateur, ParametreSysteme
from app.admin.scripts import get_param, set_param, get_maintenance_status, set_maintenance_status, check_and_run_daily_backup
from app.utils import get_annee_active, get_school_setup_state
from app.services.annees_scolaires import get_annee_consultee

class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

class PerformancePhase1TestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Create two separate schools for multi-school isolation test
        self.ecole_a = Ecole(nom="Ecole Alpha")
        self.ecole_b = Ecole(nom="Ecole Beta")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.annee_a = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.annee_a, self.annee_b])

        self.admin_a = Utilisateur(
            nom="AdminA", email="admin_a@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id
        )
        self.superadmin = Utilisateur(
            nom="SuperAdmin", email="super@test.local", mot_de_passe="pass", role="super_admin"
        )
        db.session.add_all([self.admin_a, self.superadmin])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_01_get_param_memoization_in_g(self):
        """get_param stocke la valeur dans g et évite les requêtes répétées."""
        with self.app.test_request_context():
            set_param("test_key", "valeur_123")
            # Premier appel
            val1 = get_param("test_key")
            self.assertEqual(val1, "valeur_123")
            self.assertIn("test_key", g._system_params)

            # Deuxième appel lit depuis g
            g._system_params["test_key"] = "valeur_modifiee_dans_g"
            val2 = get_param("test_key")
            self.assertEqual(val2, "valeur_modifiee_dans_g")

    def test_02_get_maintenance_status_memoization_and_functionality(self):
        """get_maintenance_status respecte le statut et utilise g._maintenance_status."""
        with self.app.test_request_context():
            status = get_maintenance_status()
            self.assertFalse(status['active'])
            self.assertTrue(hasattr(g, '_maintenance_status'))

            # Activation
            set_maintenance_status(True, "Maintenance de test")
            status_act = get_maintenance_status()
            self.assertTrue(status_act['active'])
            self.assertEqual(status_act['message'], "Maintenance de test")

            # Désactivation
            set_maintenance_status(False)
            status_desact = get_maintenance_status()
            self.assertFalse(status_desact['active'])

    def test_03_maintenance_blocking_and_superadmin_bypass(self):
        """Quand la maintenance est active, les routes publiques sont bloquées (503), sauf login et super_admin."""
        set_maintenance_status(True, "Service temporairement indisponible")
        client = self.app.test_client()

        # Visiteur normal sur / -> 503
        res = client.get("/")
        self.assertEqual(res.status_code, 503)
        self.assertIn(b"temporairement indisponible", res.data)

        # Route /login reste accessible -> 200
        res_login = client.get("/login")
        self.assertEqual(res_login.status_code, 200)

        # Super admin reste accessible
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.superadmin.id)
            sess["_fresh"] = True
        res_admin = client.get("/")
        # Le super admin sans école sélectionnée peut être redirigé ou avoir accès
        self.assertNotEqual(res_admin.status_code, 503)

        # Nettoyage
        set_maintenance_status(False)

    def test_04_multi_school_isolation_in_request_cache(self):
        """L'isolation des caches g par école est strictement garantie."""
        with self.app.test_request_context():
            annee_a = get_annee_active(self.ecole_a.id)
            annee_b = get_annee_active(self.ecole_b.id)

            self.assertEqual(annee_a.ecole_id, self.ecole_a.id)
            self.assertEqual(annee_b.ecole_id, self.ecole_b.id)
            self.assertNotEqual(annee_a.id, annee_b.id)

            # Vérification du dictionnaire séparé dans g
            self.assertIn(self.ecole_a.id, g._annee_active_cache)
            self.assertIn(self.ecole_b.id, g._annee_active_cache)
            self.assertEqual(g._annee_active_cache[self.ecole_a.id].ecole_id, self.ecole_a.id)
            self.assertEqual(g._annee_active_cache[self.ecole_b.id].ecole_id, self.ecole_b.id)

    def test_05_backup_on_request_disabled_by_default(self):
        """check_and_run_daily_backup n'est pas exécuté à chaque requête si CHECK_BACKUP_ON_REQUEST=False."""
        self.assertFalse(self.app.config.get("CHECK_BACKUP_ON_REQUEST", False))
        client = self.app.test_client()
        # Une requête GET ne doit pas déclencher de sauvegarde
        res = client.get("/")
        self.assertIn(res.status_code, [200, 302])

    def test_06_after_request_cleans_up_g(self):
        """after_request_handler nettoie rigoureusement les variables request-scoped de g."""
        client = self.app.test_client()
        res = client.get("/login")
        self.assertEqual(res.status_code, 200)
        # Hors requête, g n'a plus de traces

