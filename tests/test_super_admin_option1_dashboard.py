"""
Tests pour la Tour de Contrôle Super Admin (Option 1 PC-First) :
1. Suppression des duplications de navigation (topbar épurée, absence des 6 grosses cartes vides).
2. Présence de l'annuaire d'écoles interactif en direct sur PC avec recherche instantanée.
3. Présence de la double colonne de pilotage (Demandes Démo + Logs système).
4. Présence et fonctionnement du modal d'ajout d'école (_modal_ajouter_ecole.html) avec return_url.
5. Vue mobile épurée sans surcharge d'écran.
"""
import unittest
from datetime import datetime
from app import create_app, db
from app.models import Ecole, Utilisateur, DemandePresentation, Log
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class SuperAdminDashboardTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-superadmin-dashboard-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestSuperAdminOption1Dashboard(unittest.TestCase):
    def setUp(self):
        self.app = create_app(SuperAdminDashboardTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Création du Super Admin
        self.super_admin = Utilisateur(
            nom="Attama",
            prenom="Moctar",
            email="superadmin@klasora.ne",
            telephone="+22790000000",
            mot_de_passe=generate_password_hash("SuperPass123!"),
            role="super_admin",
            statut="actif",
        )
        db.session.add(self.super_admin)

        # Création de 2 écoles de test
        self.ecole1 = Ecole(
            nom="Lycée Fontaine",
            ville="Niamey",
            adresse="Plateau",
            telephone="90111111",
            email="direction@fontaine.ne",
            statut="actif",
            onboarding_complete=True,
        )
        self.ecole2 = Ecole(
            nom="Complexe Sahel",
            ville="Maradi",
            telephone="90222222",
            email="direction@sahel.ne",
            statut="bloque",
            onboarding_complete=False,
        )
        db.session.add_all([self.ecole1, self.ecole2])

        # Création d'une demande de présentation
        self.demande = DemandePresentation(
            nom_ecole="Collège Espoir",
            telephone="90333333",
            ville="Zinder",
            statut="nouvelle",
            created_at=datetime.utcnow()
        )
        db.session.add(self.demande)

        # Création d'un log
        self.log = Log(
            level="INFO",
            module="auth",
            action="Connexion Super Admin",
            timestamp=datetime.utcnow()
        )
        db.session.add(self.log)

        db.session.commit()
        self._login_as_super_admin()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_as_super_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.super_admin.id)
            sess['_fresh'] = True

    def test_super_admin_dashboard_content_and_cleanup(self):
        """Vérifie que la page d'accueil Super Admin intègre la Tour de Contrôle complète sans les doublons."""
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # 1. Vérifier la disparition de l'ancienne section "Actions & Pilotage Direct"
        self.assertNotIn("Actions & Pilotage Direct", html)

        # 2. Vérifier la présence du bandeau KPIs
        self.assertIn("Établissements", html)
        self.assertIn("Lycée Fontaine", html)
        self.assertIn("Complexe Sahel", html)

        # 3. Vérifier la présence de l'annuaire interactif sur PC
        self.assertIn('id="superAdminSchoolSearch"', html)
        self.assertIn('id="superAdminSchoolTableBody"', html)
        self.assertIn('super-admin-school-row', html)

        # 4. Vérifier les doubles colonnes : Demandes de démo & Logs
        self.assertIn("Demandes de Démo & Prospects", html)
        self.assertIn("Collège Espoir", html)
        self.assertIn("Derniers Journaux & Sécurité", html)
        self.assertIn("Connexion Super Admin", html)

        # 5. Vérifier la présence du modal responsive pour ajouter une école
        self.assertIn('id="modalAjouterEcole"', html)
        self.assertIn('modal-fullscreen-md-down', html)
        self.assertIn('name="nom_ecole"', html)
        self.assertIn('name="email_admin"', html)

        # 6. Vérifier la topbar sans liens redondants mais avec le statut et le bouton modal
        self.assertIn("Système Opérationnel", html)
        self.assertIn("Nouvelle École", html)

    def test_modal_ajouter_ecole_submission_and_redirect(self):
        """Vérifie qu'ajouter une école depuis la modale fonctionne et redirige vers la page d'origine."""
        data = {
            'nom_ecole': 'Académie Moderne',
            'ville': 'Niamey',
            'adresse': 'Yantala',
            'telephone': '+22790998877',
            'email_admin': 'direction@academie.ne',
            'mot_de_passe': '',
            'return_url': '/',
        }
        resp = self.client.post('/admin/ecoles/ajouter', data=data, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.location.endswith('/') or resp.location == '/')

        # Vérifier création en BDD
        ecole = Ecole.query.filter_by(nom='Académie Moderne').first()
        self.assertIsNotNone(ecole)
        self.assertEqual(ecole.ville, 'Niamey')

        admin_user = Utilisateur.query.filter_by(email='direction@academie.ne').first()
        self.assertIsNotNone(admin_user)
        self.assertEqual(admin_user.role, 'admin')
        self.assertEqual(admin_user.ecole_id, ecole.id)


    def test_get_ajouter_ecole_redirects_to_modal(self):
        """Vérifie que GET /admin/ecoles/ajouter ne charge plus de page orpheline mais redirige 302 avec auto-ouverture du modal."""
        resp = self.client.get('/admin/ecoles/ajouter', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('open_ajouter_ecole=1', resp.location)

        # Suivre la redirection
        followed = self.client.get('/admin/ecoles/ajouter', follow_redirects=True)
        self.assertEqual(followed.status_code, 200)
        html = followed.get_data(as_text=True)
        self.assertIn('id="modalAjouterEcole"', html)
        self.assertIn('open_ajouter_ecole', html)

    def test_links_trigger_modal_without_navigation(self):
        """Vérifie que la sidebar et la page de gestion des écoles pointent vers #modalAjouterEcole sans lien direct orphelin."""
        resp = self.client.get('/admin/ecoles')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Modal présent
        self.assertIn('id="modalAjouterEcole"', html)
        # Déclencheurs data-bs-target
        self.assertIn('data-bs-target="#modalAjouterEcole"', html)

    def test_topbar_button_in_right_section_with_nowrap(self):
        """Vérifie que le bouton Nouvelle École est dans la section droite avec nowrap pour éviter tout chevauchement."""
        resp = self.client.get('/admin/demandes-presentation')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Le bouton doit avoir nowrap et être dans topbar-right
        self.assertIn('white-space: nowrap !important;', html)
        self.assertIn('topbar-right', html)
        self.assertIn('modalAjouterEcole', html)

    def test_demandes_presentation_pagination_and_responsive_views(self):
        """Vérifie la pagination et la présence des vues responsive (Tableau PC + Cartes Mobile) pour les demandes."""
        # Ajouter 20 demandes supplémentaires
        for i in range(20):
            db.session.add(DemandePresentation(
                nom_ecole=f"École Test {i+1}",
                telephone=f"900000{i:02d}",
                ville="Niamey",
                statut="nouvelle" if i % 2 == 0 else "contactee",
                created_at=datetime.utcnow()
            ))
        db.session.commit()

        # 1. Vérifier la page 1 avec pagination
        resp = self.client.get('/admin/demandes-presentation?page=1')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Page 1 sur 2", html)
        self.assertIn("Suivant", html)
        # Vue PC
        self.assertIn("d-none d-md-block", html)
        # Vue Mobile
        self.assertIn("d-md-none", html)

        # 2. Vérifier la recherche
        resp_search = self.client.get('/admin/demandes-presentation?q=Espoir')
        self.assertEqual(resp_search.status_code, 200)
        html_search = resp_search.get_data(as_text=True)
        self.assertIn("Collège Espoir", html_search)
        self.assertNotIn("École Test 10", html_search)


if __name__ == '__main__':
    unittest.main()
