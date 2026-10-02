import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Ecole, Utilisateur, Eleve, Inscription, Classe, AnneeScolaire, Professeur


class ModalRechercheTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-modal-search-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestModalRechercheGlobale(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ModalRechercheTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        self.ecole = Ecole(
            nom="École Test Modal Recherche",
            statut="actif",
            onboarding_complete=True
        )
        db.session.add(self.ecole)
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.flush()

        self.admin = Utilisateur(
            email="admin_search@test.com",
            nom="Admin",
            prenom="Directeur",
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id
        )
        self.admin.mot_de_passe = generate_password_hash("AdminPass123!")
        db.session.add(self.admin)

        self.classe = Classe(nom="6ème A", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        db.session.add(self.classe)
        db.session.flush()

        self.eleve = Eleve(
            nom="Konan",
            prenom="Yao",
            date_naissance=date(2012, 5, 10),
            code_parent="YAO001",
            ecole_id=self.ecole.id
        )
        db.session.add(self.eleve)
        db.session.flush()

        self.insc = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="inscrit"
        )
        db.session.add(self.insc)

        self.prof_user = Utilisateur(
            email="prof_search@test.com",
            nom="Toure",
            prenom="Amara",
            role="professeur",
            statut="actif",
            ecole_id=self.ecole.id
        )
        self.prof_user.mot_de_passe = generate_password_hash("ProfPass123!")
        db.session.add(self.prof_user)
        db.session.flush()

        self.prof = Professeur(
            utilisateur_id=self.prof_user.id,
            nom="Toure",
            prenom="Amara",
            specialite="Mathématiques",
            ecole_id=self.ecole.id
        )
        db.session.add(self.prof)
        db.session.commit()

        # Connecter l'admin via la session
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['ecole_id'] = self.ecole.id
            sess['_fresh'] = True
            sess['onboarding_complete'] = True
            sess[f'onboarding_complete_{self.ecole.id}'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_01_recherche_sans_terme_redirection_vers_accueil_avec_open_search(self):
        """Un accès direct via URL /recherche sans requête redirige vers l'accueil pour ouvrir le modal."""
        res = self.client.get('/recherche', follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn('open_search=1', res.location)

    def test_02_recherche_avec_ajax_renvoie_partiel_html(self):
        """Une requête AJAX vers /recherche renvoie directement le fragment HTML sans rechargement de page."""
        res = self.client.get('/recherche?q=Konan&ajax=1', headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn("Konan", html)
        self.assertIn("Yao", html)
        self.assertIn("Voir le dossier", html)

    def test_03_recherche_professeur_via_ajax(self):
        """La recherche peut filtrer et trouver un professeur."""
        res = self.client.get('/recherche?q=Toure&type=professeurs&ajax=1', headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn("Toure", html)
        self.assertIn("Amara", html)
        self.assertIn("Mathématiques", html)

    def test_04_topbar_contient_la_barre_de_saisie_et_declencheur_modal(self):
        """La topbar contient la barre de saisie visible avec le déclencheur data-bs-target='#modalRechercheGlobale'."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn('topbar-search-bar', html)
        self.assertIn('#modalRechercheGlobale', html)
        self.assertIn('data-tour="topbar-recherche"', html)

    def test_05_base_contient_le_modal_recherche_et_son_script(self):
        """La structure de base inclut le modal popup et son script asynchrone."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn('id="modalRechercheGlobale"', html)
        self.assertIn('id="globalModalSearchInput"', html)
        self.assertIn('global_search_modal.js', html)

