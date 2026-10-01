"""
Tests for In-Place Modals Refactor (Architecture DRY, Zéro Code Mort, Zéro Régression Métier):
- Module 1 : Emplois du temps (#modalAjouterEmploi)
- Module 2 : Professeurs (#modalAjouterProfesseur)
- Module 3 : Élèves (#modalAjouterEleve)
- Module 4 : Paiements (#modalAjouterPaiement)
- Navbar cleanup: liens directs sans sous-liens orphelins
- Redirections 302 systématiques depuis les anciennes routes GET
"""
from datetime import date, time
import unittest

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    EmploiTemps,
    Inscription,
    NiveauScolaire,
    Professeur,
    Utilisateur,
)
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class InplaceModalsTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-inplace-modals-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestInplaceModalsRefactor(unittest.TestCase):
    def setUp(self):
        self.app = create_app(InplaceModalsTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Ecole
        self.ecole = Ecole(
            nom="Lycée d'Excellence",
            adresse="Niamey",
            telephone="90000000",
            email="direction@lycee.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Moussa",
            email="directeur@lycee.ne",
            telephone="+22790000001",
            mot_de_passe=generate_password_hash("AdminPass123!"),
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.admin)

        # Annee Active
        self.annee_active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 15),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee_active)
        db.session.flush()

        # Niveau
        self.niveau = NiveauScolaire(code="6EME", nom="Sixième", cycle="secondaire_1", ordre=1)
        db.session.add(self.niveau)
        db.session.flush()

        self.cfg_niveau = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_active.id,
            niveau_id=self.niveau.id,
            actif=True,
        )
        db.session.add(self.cfg_niveau)
        db.session.flush()

        # Classe
        self.classe = Classe(
            nom="6ème A",
            annee_scolaire_id=self.annee_active.id,
            ecole_id=self.ecole.id,
            niveau_id=self.niveau.id,
            statut="ouverte",
        )
        db.session.add(self.classe)
        db.session.flush()

        # Professeur
        self.user_prof = Utilisateur(
            nom="Saley",
            prenom="Amadou",
            telephone="+22790112233",
            mot_de_passe=generate_password_hash("87654321"),
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.user_prof)
        db.session.flush()

        self.prof = Professeur(
            nom="Saley",
            prenom="Amadou",
            telephone="+22790112233",
            specialite="Mathématiques",
            matieres_enseignees="Mathématiques",
            code_prof="87654321",
            ecole_id=self.ecole.id,
            utilisateur_id=self.user_prof.id,
        )
        db.session.add(self.prof)
        db.session.flush()

        # Cours
        self.cours = Cours(
            nom="Mathématiques",
            classe_id=self.classe.id,
            professeur_id=self.prof.id,
            coefficient=3.0,
            ecole_id=self.ecole.id,
        )
        db.session.add(self.cours)
        db.session.commit()

        # Login
        self._login_as_admin()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_as_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['ecole_id'] = self.ecole.id
            sess['_fresh'] = True

    def test_eleves_mother_page_and_modal(self):
        """Vérifie la présence de #modalAjouterEleve sur /eleves et la redirection de /ajouter_eleve"""
        # 1. Page mère contient la modale et les attributs requis
        resp = self.client.get('/eleves')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="modalAjouterEleve"', html)
        self.assertIn('data-bs-backdrop="static"', html)
        self.assertIn('data-bs-target="#modalAjouterEleve"', html)
        self.assertIn('modal-xl', html)

        # 2. GET /ajouter_eleve redirige vers /eleves (302)
        resp_get = self.client.get('/ajouter_eleve')
        self.assertEqual(resp_get.status_code, 302)
        self.assertTrue(resp_get.location.endswith('/eleves') or '/eleves' in resp_get.location)

        # 3. POST /ajouter_eleve enregistre l'élève et redirige vers /eleves
        data = {
            'nom': 'Oumarou',
            'prenom': 'Fatima',
            'genre': 'F',
            'date_naissance': '2012-05-10',
            'lieu_naissance': 'Niamey',
            'classe_id': self.classe.id,
            'frais_annuels': 150000,
            'parent_id': 0,
            'parent_nom': 'Oumarou Souleymane',
            'parent_telephone': '+22791223344',
            'code_parent': '12345678',
            'adresse': 'Quartier Plateau',
        }
        resp_post = self.client.post('/ajouter_eleve', data=data, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)

        # Vérifier en base
        eleve = Eleve.query.filter_by(nom='Oumarou', prenom='Fatima').first()
        self.assertIsNotNone(eleve)
        self.assertEqual(eleve.genre, 'F')
        ins = Inscription.query.filter_by(eleve_id=eleve.id).first()
        self.assertIsNotNone(ins)
        self.assertEqual(ins.classe_id, self.classe.id)

    def test_professeurs_mother_page_and_modal(self):
        """Vérifie la présence de #modalAjouterProfesseur sur /professeurs et la redirection de /ajouter_professeur"""
        # 1. Page mère contient la modale
        resp = self.client.get('/professeurs')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="modalAjouterProfesseur"', html)
        self.assertIn('data-bs-backdrop="static"', html)
        self.assertIn('data-bs-target="#modalAjouterProfesseur"', html)

        # 2. GET /ajouter_professeur redirige vers /professeurs (302)
        resp_get = self.client.get('/ajouter_professeur')
        self.assertEqual(resp_get.status_code, 302)
        self.assertTrue(resp_get.location.endswith('/professeurs') or '/professeurs' in resp_get.location)

        # 3. POST /ajouter_professeur enregistre le professeur et redirige vers /professeurs
        data = {
            'nom': 'Kane',
            'prenom': 'Aissata',
            'telephone': '+22792334455',
            'date_naissance': '1985-04-12',
            'adresse': 'Dar Es Salam',
            'specialite': 'Physique-Chimie',
            'matieres_enseignees': 'Physique-Chimie',
            'code_prof': '98765432',
        }
        resp_post = self.client.post('/ajouter_professeur', data=data, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)

        prof = Professeur.query.filter_by(nom='Kane', prenom='Aissata').first()
        self.assertIsNotNone(prof)
        self.assertEqual(prof.code_prof, '98765432')

    def test_emplois_mother_page_and_modal(self):
        """Vérifie la présence de #modalAjouterEmploi sur /admin/emplois et la redirection de /admin/ajouter_emploi"""
        # 1. Page mère contient la modale
        resp = self.client.get('/admin/emplois')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="modalAjouterEmploi"', html)
        self.assertIn('data-bs-backdrop="static"', html)
        self.assertIn('data-bs-target="#modalAjouterEmploi"', html)

        # 2. GET /admin/ajouter_emploi redirige vers /admin/emplois (302)
        resp_get = self.client.get('/admin/ajouter_emploi')
        self.assertEqual(resp_get.status_code, 302)
        self.assertTrue('/admin/emplois' in resp_get.location or '/emplois' in resp_get.location)

        # 3. POST /admin/ajouter_emploi crée le créneau
        data = {
            'classe_id': self.classe.id,
            'professeur_id': self.prof.id,
            'cours_id': self.cours.id,
            'jour': 'Lundi',
            'heure_debut': '08:00',
            'heure_fin': '10:00',
            'salle': 'Salle 12',
        }
        resp_post = self.client.post('/admin/ajouter_emploi', data=data, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)

        creneau = EmploiTemps.query.filter_by(classe_id=self.classe.id, cours_id=self.cours.id, jour='Lundi').first()
        self.assertIsNotNone(creneau)
        self.assertEqual(creneau.salle, 'Salle 12')

    def test_paiements_mother_page_and_modal(self):
        """Vérifie la présence de #modalAjouterPaiement sur /paiements et le quick-pay"""
        # Inscrire un élève d'abord
        eleve = Eleve(
            nom='Bello',
            prenom='Ibrahim',
            genre='M',
            date_naissance=date(2012, 1, 1),
            ecole_id=self.ecole.id,
        )
        db.session.add(eleve)
        db.session.flush()

        ins = Inscription(
            eleve_id=eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee_active.id,
            ecole_id=self.ecole.id,
            statut='inscrit',
            frais_annuels=150000,
        )
        db.session.add(ins)
        db.session.commit()

        # 1. Page mère contient la modale et n'a plus l'ancien collapse
        resp = self.client.get('/paiements')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="modalAjouterPaiement"', html)
        self.assertIn('data-bs-backdrop="static"', html)
        self.assertIn('data-bs-target="#modalAjouterPaiement"', html)
        self.assertNotIn('id="collapseNouveauPaiement"', html)

        # 2. POST /paiements enregistre le versement
        data = {
            'eleve_id': eleve.id,
            'montant': 50000,
            'mode_paiement': 'Espèces',
            'mois': 'Octobre',
            'annee': 2025,
            'reference': 'REC-2025-001',
        }
        resp_post = self.client.post('/paiements', data=data, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)

    def test_navbar_direct_links(self):
        """Vérifie que la navbar a été nettoyée des sous-liens orphelins + Ajouter..."""
        resp = self.client.get('/eleves')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # Ne doit plus contenir les sous-liens orphelins de navbar vers ajouter_eleve et ajouter_professeur
        self.assertNotIn('<li><a class="dropdown-item" href="/ajouter_eleve">', html)
        self.assertNotIn('<li><a class="dropdown-item" href="/ajouter_professeur">', html)
        # Liens directs dans la navbar
        self.assertIn('href="/eleves"', html)
        self.assertIn('href="/professeurs"', html)

    def test_classes_mother_page_and_modal(self):
        """Vérifie l'intégration in-place de la modale d'ajout classe et la suppression de la page autonome."""
        # 1. Page mère contient la modale avec attributs requis
        resp = self.client.get('/classes')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="modalAjouterClasse"', html)
        self.assertIn('data-bs-backdrop="static"', html)
        self.assertIn('data-bs-keyboard="false"', html)
        self.assertIn('data-bs-target="#modalAjouterClasse"', html)

        # 2. Redirections 302 sur GET historique
        resp_get_add = self.client.get('/classes/add', follow_redirects=False)
        self.assertEqual(resp_get_add.status_code, 302)
        self.assertIn('/classes', resp_get_add.headers['Location'])

        resp_get_alias = self.client.get('/ajouter_classe', follow_redirects=False)
        self.assertEqual(resp_get_alias.status_code, 302)
        self.assertIn('/classes', resp_get_alias.headers['Location'])

        # 3. POST direct enregistre la classe
        data = {
            'niveau_id': self.niveau.id,
            'section': 'B',
            'capacite': 30,
            'professeur_principal_id': self.prof.id,
            'annee_scolaire_id': self.annee_active.id,
        }
        resp_post = self.client.post('/classes/add', data=data, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)
        created_cls = Classe.query.filter_by(ecole_id=self.ecole.id, section='B').first()
        self.assertIsNotNone(created_cls)

    def test_cours_mother_page_and_modal(self):
        """Vérifie l'intégration in-place de la modale d'ajout cours et le remplacement du collapse."""
        # 1. Page mère contient la modale et n'a plus l'ancien collapse
        resp = self.client.get('/cours')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="modalAjouterCours"', html)
        self.assertIn('data-bs-backdrop="static"', html)
        self.assertIn('data-bs-keyboard="false"', html)
        self.assertIn('data-bs-target="#modalAjouterCours"', html)
        self.assertNotIn('id="collapseAjouterCours"', html)

        # 2. Redirection 302 sur GET /ajouter_cours
        resp_get = self.client.get('/ajouter_cours', follow_redirects=False)
        self.assertEqual(resp_get.status_code, 302)
        self.assertIn('/cours', resp_get.headers['Location'])

        # 3. POST /ajouter_cours crée le cours
        data = {
            'nom': 'Histoire-Géographie',
            'coefficient': 2,
            'classe_id': self.classe.id,
            'professeur_id': self.prof.id,
            'description': 'Programme officiel',
        }
        resp_post = self.client.post('/ajouter_cours', data=data, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)
        created_cours = Cours.query.filter_by(ecole_id=self.ecole.id, nom='Histoire-Géographie').first()
        self.assertIsNotNone(created_cours)
        self.assertEqual(created_cours.coefficient, 2)


if __name__ == '__main__':
    unittest.main()
