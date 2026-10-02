import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Utilisateur


class ModalCentreAlertesTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-modal-centre-alertes-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestModalCentreAlertes(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ModalCentreAlertesTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        self.ecole = Ecole(
            nom="École Modal Alertes Test",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()

        self.admin = Utilisateur(
            email="admin_modal_alertes@test.com",
            nom="Admin",
            prenom="Directeur",
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        self.admin.mot_de_passe = generate_password_hash("AdminModal123!")
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_modal_partial_template_structure(self):
        """Vérifie la présence et la structure du template partiel _modal_centre_alertes.html."""
        with open('app/templates/partials/_modal_centre_alertes.html', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('id="modalCentreAlertes"', content)
        self.assertIn('id="modalAlertesHeaderBadge"', content)
        self.assertIn('id="btnModalMarkAllRead"', content)
        self.assertIn('data-filter="jamais_paye"', content)
        self.assertIn('id="mTabCountJamaisPaye"', content)
        self.assertIn('id="mTabCountPaiements"', content)
        self.assertIn('id="mTabCountAbsences"', content)
        self.assertIn('id="mTabCountNotes"', content)
        self.assertIn('id="modalAlertesSearchInput"', content)
        self.assertIn('id="modalAlertesClassFilter"', content)
        self.assertIn('id="modalAlertesList"', content)

    def test_topbar_alertes_button_presence(self):
        """Vérifie la présence du bouton Alertes avec badge dans _topbar_admin.html."""
        with open('app/templates/partials/_topbar_admin.html', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('data-bs-target="#modalCentreAlertes"', content)
        self.assertIn('id="topbarAlertesBadge"', content)
        self.assertIn('id="btnTopbarAlertes"', content)
        self.assertIn('Visite guidée', content)

    def test_base_html_includes_modal_and_script(self):
        """Vérifie que base.html inclut le composant modal et son script JS."""
        with open('app/templates/base.html', 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn("partials/_modal_centre_alertes.html", content)
        self.assertIn("modal_centre_alertes.js", content)

    def test_modal_rendered_in_index_response(self):
        """Vérifie que le modal et le bouton topbar sont bien rendus dans la réponse de la page index."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole.id

        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn('id="modalCentreAlertes"', html)
        self.assertIn('id="topbarAlertesBadge"', html)
        self.assertIn('modal_centre_alertes.js', html)

    def test_alertes_redirect_to_index_with_open_alertes(self):
        """Vérifie que la route /alertes renvoie une redirection 302 vers /?open_alertes=1."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole.id

        resp = self.client.get('/alertes', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('open_alertes=1', resp.headers.get('Location', ''))

        # En suivant la redirection
        follow_resp = self.client.get('/alertes', follow_redirects=True)
        self.assertEqual(follow_resp.status_code, 200)
        html = follow_resp.get_data(as_text=True)
        self.assertIn('id="modalCentreAlertes"', html)


if __name__ == '__main__':
    unittest.main()

