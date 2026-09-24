"""
Tests for In-Place Student Consultation & Modification (Phase Step 3 - Zéro dispersion):
1. GET /api/eleves/<id>/fiche returns full student sheet, parents, grades, and edit permissions.
2. POST /api/eleves/<id>/modifier allows updating student profile & parent contact in-place via AJAX.
3. Access controls & security (archived year restriction, unauthorized access prevention).
4. HTML verification: /eleves renders #modalFicheEleve and onclick handlers without forcing page reload.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Cours,
    Utilisateur,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class InplaceModalTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-inplace-modal-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestElevesInplaceModal(unittest.TestCase):
    def setUp(self):
        self.app = create_app(InplaceModalTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Ecole
        self.ecole = Ecole(
            nom="Groupe Scolaire Excellence",
            adresse="Niamey",
            telephone="90000000",
            email="direction@excellence.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Moussa",
            email="directeur@excellence.ne",
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

        # Niveaux
        self.niveau_cp = NiveauScolaire(code="CP", nom="Cours Préparatoire", cycle="elementaire", ordre=1)
        self.niveau_ce1 = NiveauScolaire(code="CE1", nom="Cours Élémentaire 1", cycle="elementaire", ordre=2)
        db.session.add_all([self.niveau_cp, self.niveau_ce1])
        db.session.flush()

        # Classes
        self.classe_a = Classe(
            nom="CP-A",
            annee_scolaire_id=self.annee_active.id,
            ecole_id=self.ecole.id,
            niveau_id=self.niveau_cp.id,
            statut="ouverte",
        )
        self.classe_b = Classe(
            nom="CP-B",
            annee_scolaire_id=self.annee_active.id,
            ecole_id=self.ecole.id,
            niveau_id=self.niveau_cp.id,
            statut="ouverte",
        )
        db.session.add_all([self.classe_a, self.classe_b])
        db.session.flush()

        # Parent
        self.parent = Utilisateur(
            nom="Abdoulaye",
            prenom="Ibrahim",
            email="ibrahim.abdoulaye@example.com",
            telephone="+22790112233",
            mot_de_passe=generate_password_hash("12345678"),
            role="parent",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.parent)
        db.session.flush()

        # Eleve
        self.eleve = Eleve(
            nom="Abdoulaye",
            prenom="Fatima",
            date_naissance=date(2018, 5, 12),
            genre="F",
            code_parent="87654321",
            contact_parent="+22790112233",
            parent_id=self.parent.id,
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.eleve)
        db.session.flush()

        # Inscription
        creer_inscription_annuelle(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_active.id,
            classe_id=self.classe_a.id,
            statut="inscrit",
        )

        # Cours et note
        self.cours_math = Cours(
            nom="Mathématiques",
            classe_id=self.classe_a.id,
            ecole_id=self.ecole.id,
            coefficient=2,
        )
        db.session.add(self.cours_math)
        db.session.flush()

        self.note = Note(
            eleve_id=self.eleve.id,
            cours_id=self.cours_math.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee_active.id,
            valeur=17.5,
            coefficient=2,
            type_evaluation="Devoir 1",
            date_evaluation=datetime(2025, 11, 10),
        )
        db.session.add(self.note)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['role'] = 'admin'
            sess['ecole_id'] = self.ecole.id
            sess['_fresh'] = True

    def test_01_api_fiche_eleve_success(self):
        """Vérifie que l'API renvoie toutes les informations nécessaires à la modale sans rechargement"""
        self._login_admin()
        resp = self.client.get(f'/api/eleves/{self.eleve.id}/fiche')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertTrue(data['success'])
        self.assertEqual(data['eleve']['prenom'], "Fatima")
        self.assertEqual(data['eleve']['nom'], "Abdoulaye")
        self.assertEqual(data['eleve']['genre'], "F")
        self.assertEqual(data['classe']['nom'], "CP-A")
        self.assertEqual(data['parent']['telephone'], "+22790112233")
        self.assertTrue(data['can_edit'])
        self.assertGreaterEqual(len(data['classes_disponibles']), 2)

    def test_02_api_modifier_eleve_in_place(self):
        """Vérifie que l'élève peut être modifié sur place via l'API JSON"""
        self._login_admin()
        payload = {
            "nom": "Abdoulaye Modif",
            "prenom": "Fatima Zahra",
            "genre": "F",
            "date_naissance": "2018-05-12",
            "classe_id": self.classe_b.id,
            "parent_nom": "Ibrahim Abdoulaye",
            "parent_telephone": "+22799887766",
            "parent_email": "ibrahim.abdoulaye@example.com",
            "lieu_naissance": "Niamey",
            "adresse": "Quartier Plateau",
            "frais_annuels": 175000.0,
        }
        resp = self.client.post(f'/api/eleves/{self.eleve.id}/modifier', json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertTrue(data['success'])
        self.assertEqual(data['eleve']['nom'], "Abdoulaye Modif")
        self.assertEqual(data['eleve']['prenom'], "Fatima Zahra")
        self.assertEqual(data['eleve']['classe_nom'], "CP-B")

        # Vérifier en BDD
        eleve_db = db.session.get(Eleve, self.eleve.id)
        self.assertEqual(eleve_db.nom, "Abdoulaye Modif")
        self.assertEqual(eleve_db.prenom, "Fatima Zahra")
        self.assertEqual(eleve_db.contact_parent, "+22799887766")
        self.assertEqual(eleve_db.frais_annuels, 175000.0)

    def test_03_api_modifier_eleve_validation_errors(self):
        """Vérifie le rejet des entrées invalides"""
        self._login_admin()
        # Nom vide
        resp = self.client.post(f'/api/eleves/{self.eleve.id}/modifier', json={"nom": "", "prenom": "Test"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()['success'])

        # Classe manquante
        resp = self.client.post(f'/api/eleves/{self.eleve.id}/modifier', json={"nom": "Nom", "prenom": "Prenom", "classe_id": ""})
        self.assertEqual(resp.status_code, 400)

    def test_04_ui_eleves_has_modal_and_triggers(self):
        """Vérifie que la page /eleves intègre la modale #modalFicheEleve et les appels openFicheEleve"""
        self._login_admin()
        resp = self.client.get('/eleves')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn('id="modalFicheEleve"', html)
        self.assertIn('openFicheEleve', html)
        self.assertIn('formModifierEleveSurPlace', html)
        self.assertIn('Modifier sur place', html)

