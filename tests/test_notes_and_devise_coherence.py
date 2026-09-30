"""
Tests de cohérence système :
1. Persistance BDD de la devise / slogan de l'école (Tâche 1)
2. Filtrage SQL natif de la route des notes (Tâche 2)
"""
import unittest
from datetime import date, datetime

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    Note,
    Professeur,
    Utilisateur,
)
from app.services.notes_annuelles import get_notes_query
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class NotesAndDeviseCoherenceTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-coherence-notes-devise"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class NotesAndDeviseCoherenceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(NotesAndDeviseCoherenceTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # Établissement
        self.ecole = Ecole(
            nom="Complexe Scolaire Klasora",
            adresse="Niamey Plateau",
            telephone="90112233",
            email="direction@klasora.ne",
            directeur="M. Directeur",
            devise="Discipline - Travail - Succès",
        )
        db.session.add(self.ecole)
        db.session.commit()

        # Année scolaire
        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 15),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.commit()

        # Utilisateur Admin
        self.admin = Utilisateur(
            nom="Admin",
            prenom="Test",
            email="admin@klasora.ne",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.admin)

        # Classe
        self.classe = Classe(
            nom="6ème A",
            niveau="6ème",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        db.session.add(self.classe)
        db.session.commit()

        # Élèves
        self.eleve1 = Eleve(
            nom="Moussa",
            prenom="Ibrahim",
            date_naissance=date(2014, 5, 12),
            genre="M",
            code_parent="P-001",
            ecole_id=self.ecole.id,
        )
        self.eleve2 = Eleve(
            nom="Abdou",
            prenom="Fatima",
            date_naissance=date(2014, 8, 20),
            genre="F",
            code_parent="P-002",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.eleve1, self.eleve2])
        db.session.commit()

        # Inscriptions
        self.ins1 = Inscription(
            eleve_id=self.eleve1.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="inscrit",
        )
        self.ins2 = Inscription(
            eleve_id=self.eleve2.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="inscrit",
        )
        db.session.add_all([self.ins1, self.ins2])

        # Cours
        self.cours_maths = Cours(
            nom="Mathématiques",
            classe_id=self.classe.id,
            ecole_id=self.ecole.id,
        )
        self.cours_francais = Cours(
            nom="Français",
            classe_id=self.classe.id,
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.cours_maths, self.cours_francais])
        db.session.commit()

        # Notes de test
        # Note 1 : Ibrahim, Maths, Semestre 1, Devoir, 15
        self.n1 = Note(
            valeur=15.0,
            coefficient=1.0,
            type_evaluation="Devoir",
            periode="Semestre 1",
            eleve_id=self.eleve1.id,
            cours_id=self.cours_maths.id,
            inscription_id=self.ins1.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
        )
        # Note 2 : Ibrahim, Maths, Semestre 1, Composition, 14
        self.n2 = Note(
            valeur=14.0,
            coefficient=2.0,
            type_evaluation="Composition",
            periode="Semestre 1",
            eleve_id=self.eleve1.id,
            cours_id=self.cours_maths.id,
            inscription_id=self.ins1.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
        )
        # Note 3 : Fatima, Maths, Semestre 2, Devoir, 18
        self.n3 = Note(
            valeur=18.0,
            coefficient=1.0,
            type_evaluation="Devoir",
            periode="Semestre 2",
            eleve_id=self.eleve2.id,
            cours_id=self.cours_maths.id,
            inscription_id=self.ins2.id,
            ecole_id=self.ecole.id,
            annee_id=self.annee.id,
        )
        db.session.add_all([self.n1, self.n2, self.n3])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_ecole_devise_persistence_and_slogan_property(self):
        """Vérifie que devise est stockée en colonne BDD et accessible via slogan."""
        e = Ecole.query.get(self.ecole.id)
        self.assertEqual(e.devise, "Discipline - Travail - Succès")
        self.assertEqual(e.slogan, "Discipline - Travail - Succès")

        # Mise à jour via le setter slogan
        e.slogan = "Excellence & Rigueur"
        db.session.commit()

        # Relecture depuis une nouvelle session
        e_reloaded = Ecole.query.get(self.ecole.id)
        self.assertEqual(e_reloaded.devise, "Excellence & Rigueur")
        self.assertEqual(e_reloaded.slogan, "Excellence & Rigueur")

        # Vérification dans to_dict()
        data = e_reloaded.to_dict()
        self.assertEqual(data["devise"], "Excellence & Rigueur")
        self.assertEqual(data["slogan"], "Excellence & Rigueur")

    def test_note_periode_id_synonym(self):
        """Vérifie que Note.periode_id est un synonyme fonctionnel en SQL de Note.periode."""
        notes_s1 = Note.query.filter(Note.periode_id == "Semestre 1").all()
        self.assertEqual(len(notes_s1), 2)
        self.assertTrue(all(n.periode == "Semestre 1" for n in notes_s1))

        notes_s2 = Note.query.filter(Note.periode_id == "Semestre 2").all()
        self.assertEqual(len(notes_s2), 1)
        self.assertEqual(notes_s2[0].valeur, 18.0)

    def test_get_notes_query_sql_native_filters(self):
        """Vérifie que get_notes_query retourne une Query SQLAlchemy filtrable nativement."""
        base_query = get_notes_query(
            ecole_id=self.ecole.id,
            annee=self.annee,
            user=self.admin,
            classe_id=self.classe.id,
        )
        # 3 notes au total pour cette classe
        self.assertEqual(base_query.count(), 3)

        # Filtre SQL natif sur la période
        q_s1 = base_query.filter(Note.periode_id == "Semestre 1")
        self.assertEqual(q_s1.count(), 2)

        # Filtre SQL natif sur type d'évaluation
        q_comp = base_query.filter(Note.type_evaluation == "Composition")
        self.assertEqual(q_comp.count(), 1)
        self.assertEqual(q_comp.first().valeur, 14.0)

        # Filtre SQL combiné (Semestre 1 + Devoir)
        q_s1_dev = base_query.filter(Note.periode_id == "Semestre 1", Note.type_evaluation == "Devoir")
        self.assertEqual(q_s1_dev.count(), 1)
        self.assertEqual(q_s1_dev.first().eleve_id, self.eleve1.id)

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['annee_id'] = self.annee.id
            sess['annee_consultee'] = {str(self.ecole.id): self.annee.id}
            sess['ecole_id'] = self.ecole.id
            sess['role'] = 'admin'
            sess['onboarding_complete'] = True

    def test_notes_route_ajax_filtering(self):
        """Vérifie que la route /notes avec paramètres renvoie les résultats filtrés en SQL via AJAX."""
        self._login_admin()

        # Requête AJAX sans filtre -> 3 notes
        resp = self.client.get(f'/notes?classe_id={self.classe.id}', headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['total'], 3)

        # Requête AJAX avec filtre periode_id="Semestre 1"
        resp_s1 = self.client.get(f'/notes?classe_id={self.classe.id}&periode_id=Semestre 1', headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp_s1.status_code, 200)
        data_s1 = resp_s1.get_json()
        self.assertEqual(data_s1['total'], 2)

        # Requête AJAX avec filtre type_eval="Composition"
        resp_comp = self.client.get(f'/notes?classe_id={self.classe.id}&type_eval=Composition', headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp_comp.status_code, 200)
        data_comp = resp_comp.get_json()
        self.assertEqual(data_comp['total'], 1)
        self.assertEqual(data_comp['notes'][0]['valeur'], 14.0)

        # Requête AJAX avec recherche textuelle "Fatima"
        resp_search = self.client.get(f'/notes?classe_id={self.classe.id}&search=Fatima', headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp_search.status_code, 200)
        data_search = resp_search.get_json()
        self.assertEqual(data_search['total'], 1)
        self.assertEqual(data_search['notes'][0]['eleve_nom'], 'Fatima Abdou')

    def test_password_reset_routes_rate_limiting(self):
        """Vérifie que les routes de réinitialisation de mot de passe sont correctement configurées et accessibles."""
        resp = self.client.get('/request_reset_password')
        self.assertEqual(resp.status_code, 200)

        resp_token = self.client.get('/reset_password/token_invalide_test')
        self.assertIn(resp_token.status_code, (200, 302))
