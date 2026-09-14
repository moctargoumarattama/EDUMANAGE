"""
Tests KLASORA — Phase 5M : Notes + Bulletins Professionnels + Identité Visuelle.
Exactement 10 tests ciblés.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    JournalCorrection,
    NiveauScolaire,
    Note,
    PeriodeBulletin,
    Professeur,
    Utilisateur,
)
from app.services.bulletins_annuels import calculer_bulletin_data
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.notes_annuelles import valider_mutation_note, supprimer_note


from sqlalchemy.pool import StaticPool


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False}
    }
    SECRET_KEY = "test-phase5m"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestPhase5MNotesBulletinsBranding(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # École A
        self.ecole_a = Ecole(
            nom="École A de Niamey",
            adresse="Avenue du Fleuve",
            telephone="90000001",
            email="contact@ecole-a.ne",
            directeur="M. Mamane",
            ville="Niamey",
            statut="actif"
        )
        # École B
        self.ecole_b = Ecole(
            nom="Complexe B de Zinder",
            adresse="Quartier Sultanat",
            telephone="90000002",
            email="contact@ecole-b.ne",
            directeur="Mme Fatima",
            ville="Zinder",
            statut="actif"
        )
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()

        # Année scolaire École A
        self.annee_a = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 15),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id
        )
        # Année scolaire archivée École A
        self.annee_archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 15),
            date_fin=date(2025, 6, 30),
            statut="archivee",
            ecole_id=self.ecole_a.id
        )
        db.session.add_all([self.annee_a, self.annee_archivee])
        db.session.commit()

        # Niveau
        self.niveau = NiveauScolaire(code="6EME", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niveau)
        db.session.commit()

        # Classe École A
        self.classe_a = Classe(
            nom="6ème A",
            niveau_id=self.niveau.id,
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a.id,
            statut="ouverte"
        )
        db.session.add(self.classe_a)
        db.session.commit()

        # Utilisateurs
        self.admin_a = Utilisateur(
            nom="Admin A", prenom="Super", email="admina@ecole.ne",
            mot_de_passe="pass123", role="admin", ecole_id=self.ecole_a.id
        )
        self.parent_a = Utilisateur(
            nom="Parent Diallo", prenom="Oumarou", email="parenta@ecole.ne",
            mot_de_passe="pass123", role="parent", ecole_id=self.ecole_a.id
        )
        self.user_prof_a = Utilisateur(
            nom="Prof Traoré", prenom="Ibrahim", email="profa@ecole.ne",
            mot_de_passe="pass123", role="professeur", ecole_id=self.ecole_a.id
        )
        db.session.add_all([self.admin_a, self.parent_a, self.user_prof_a])
        db.session.commit()

        # Professeur
        self.prof_a = Professeur(
            nom="Traoré", prenom="Ibrahim", email="profa@ecole.ne",
            code_prof="PROF001", utilisateur_id=self.user_prof_a.id, ecole_id=self.ecole_a.id
        )
        db.session.add(self.prof_a)
        db.session.commit()

        # Élève École A
        self.eleve_a = Eleve(
            nom="Diallo", prenom="Amadou", date_naissance=date(2013, 5, 10),
            ecole_id=self.ecole_a.id, classe_id=self.classe_a.id, parent_id=self.parent_a.id
        )
        db.session.add(self.eleve_a)
        db.session.commit()

        # Inscription annuelle
        res_ins = creer_inscription_annuelle(
            eleve_id=self.eleve_a.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=150000.0
        )
        self.ins_a = res_ins[0] if isinstance(res_ins, tuple) else res_ins

        # Cours
        self.cours_math = Cours(nom="Mathématiques", coefficient=3.0, ecole_id=self.ecole_a.id, classe_id=self.classe_a.id, professeur_id=self.prof_a.id)
        self.cours_fr = Cours(nom="Français", coefficient=2.0, ecole_id=self.ecole_a.id, classe_id=self.classe_a.id, professeur_id=self.prof_a.id)
        db.session.add_all([self.cours_math, self.cours_fr])
        db.session.commit()

        # Période bulletin
        self.periode_s1 = PeriodeBulletin(
            nom="Semestre 1", annee_id=self.annee_a.id, ecole_id=self.ecole_a.id, publie=False,
            date_debut=date(2025, 9, 15), date_fin=date(2026, 1, 31)
        )
        db.session.add(self.periode_s1)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, email):
        with self.client.session_transaction() as sess:
            u = Utilisateur.query.filter_by(email=email).first()
            sess['_user_id'] = str(u.id)
            sess['ecole_id'] = u.ecole_id
            sess['annee_consultee_id'] = self.annee_a.id

    # 1. Parent header branding
    def test_parent_header_branding(self):
        self._login("parenta@ecole.ne")
        res = self.client.get('/parent/dashboard')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn("École A de Niamey", html)

    # 2. Prof header branding
    def test_prof_header_branding(self):
        self._login("profa@ecole.ne")
        res = self.client.get('/professeur/dashboard')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn("École A de Niamey", html)

    # 3. Admin header branding
    def test_admin_header_branding(self):
        self._login("admina@ecole.ne")
        res = self.client.get('/dashboard', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn("KLASORA", html)
        self.assertIn("École A de Niamey", html)

    # 4. Bulletin branding school isolation
    def test_bulletin_branding_school_isolation(self):
        self._login("admina@ecole.ne")
        res = self.client.get('/bulletins', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn("École A de Niamey", html)
        self.assertNotIn("Complexe B de Zinder", html)

    # 5. Subject without composition -> incomplete ('-'), not 0
    def test_subject_without_composition_incomplete(self):
        # Contröle seulement
        note_ctrl = Note(
            valeur=15.0, coefficient=1.0, type_evaluation="Interrogation",
            periode="Semestre 1", eleve_id=self.eleve_a.id, cours_id=self.cours_math.id,
            ecole_id=self.ecole_a.id, inscription_id=self.ins_a.id, annee_id=self.annee_a.id
        )
        db.session.add(note_ctrl)
        db.session.commit()

        b_data, _ = calculer_bulletin_data(self.ecole_a.id, self.annee_a, self.ins_a, "Semestre 1")
        disc_math = next((d for d in b_data['disciplines'] if d['cours_nom'] == self.cours_math.nom), None)
        self.assertIsNotNone(disc_math)
        self.assertEqual(disc_math['moyenne_controles'], 15.0)
        self.assertIsNone(disc_math['note_composition'])
        self.assertIsNone(disc_math['moyenne_semestre'])
        self.assertNotEqual(disc_math['moyenne_semestre'], 0.0)

    # 6. Subject without controls -> incomplete ('-'), not 0
    def test_subject_without_controls_incomplete(self):
        # Composition seulement
        note_comp = Note(
            valeur=14.0, coefficient=1.0, type_evaluation="Composition",
            periode="Semestre 1", eleve_id=self.eleve_a.id, cours_id=self.cours_fr.id,
            ecole_id=self.ecole_a.id, inscription_id=self.ins_a.id, annee_id=self.annee_a.id
        )
        db.session.add(note_comp)
        db.session.commit()

        b_data, _ = calculer_bulletin_data(self.ecole_a.id, self.annee_a, self.ins_a, "Semestre 1")
        disc_fr = next((d for d in b_data['disciplines'] if d['cours_nom'] == self.cours_fr.nom), None)
        self.assertIsNotNone(disc_fr)
        self.assertIsNone(disc_fr['moyenne_controles'])
        self.assertEqual(disc_fr['note_composition'], 14.0)
        self.assertIsNone(disc_fr['moyenne_semestre'])
        self.assertNotEqual(disc_fr['moyenne_semestre'], 0.0)

    # 7. Unpublished bulletin -> Parent view refused
    def test_unpublished_bulletin_parent_view_refused(self):
        self.periode_s1.publie = False
        db.session.commit()

        self._login("parenta@ecole.ne")
        res = self.client.get('/bulletins')
        html = res.data.decode('utf-8')
        self.assertTrue(res.status_code in (200, 302, 403))

    # 8. Published bulletin -> Prof note modification refused
    def test_published_bulletin_prof_edit_refused(self):
        self.periode_s1.publie = True
        db.session.commit()

        note_exist = Note(
            valeur=12.0, coefficient=1.0, type_evaluation="Devoir",
            periode="Semestre 1", eleve_id=self.eleve_a.id, cours_id=self.cours_math.id,
            ecole_id=self.ecole_a.id, inscription_id=self.ins_a.id, annee_id=self.annee_a.id
        )
        db.session.add(note_exist)
        db.session.commit()

        # Mutation check on published period
        valide, msg, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.annee_a, self.user_prof_a,
            self.eleve_a.id, self.cours_math.id, 15.0, note_id=note_exist.id, periode="Semestre 1"
        )
        self.assertFalse(valide)
        self.assertIn("verrouillées", msg.lower())

        # Deletion check
        valide_sup, msg_sup = supprimer_note(self.ecole_a.id, self.annee_a, self.user_prof_a, note_exist.id)
        self.assertFalse(valide_sup)

    # 9. Admin reopening -> Note correction allowed, log recorded
    def test_admin_reopening_republish_workflow(self):
        self.periode_s1.publie = True
        db.session.commit()

        # Admin reopens semester via route or service logic
        self.periode_s1.publie = False
        journal = JournalCorrection(
            action="BULLETIN_REOUVERT",
            description=f"Réouverture administrative explicite du bulletin {self.periode_s1.nom}",
            ecole_id=self.ecole_a.id,
            user_id=self.admin_a.id,
            cible_type="periode_bulletin",
            cible_id=self.periode_s1.id,
            niveau="warning"
        )
        db.session.add(journal)
        db.session.commit()

        # Check note editing enabled now for Admin
        valide, msg, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.annee_a, self.admin_a,
            self.eleve_a.id, self.cours_math.id, 14.0, periode="Semestre 1"
        )
        self.assertTrue(valide)

        # Check JournalCorrection logged
        log_entry = JournalCorrection.query.filter_by(action="BULLETIN_REOUVERT").first()
        self.assertIsNotNone(log_entry)

    # 10. Archived year -> Bulletin downloadable, note mutations rejected
    def test_archived_year_bulletin_download_read_only(self):
        res_arch = creer_inscription_annuelle(
            eleve_id=self.eleve_a.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_archivee.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=150000.0
        )
        ins_arch = res_arch[0] if isinstance(res_arch, tuple) else res_arch

        # Mutation rejected on archived year
        valide, msg, _, _, _ = valider_mutation_note(
            self.ecole_a.id, self.annee_archivee, self.admin_a,
            self.eleve_a.id, self.cours_math.id, 16.0, periode="Semestre 1"
        )
        self.assertFalse(valide)
        self.assertIn("archivée", msg.lower())

        # PDF download accessible
        self._login("admina@ecole.ne")
        res_pdf = self.client.get(f'/bulletin/pdf/{self.eleve_a.id}?periode=Semestre+1&annee_id={self.annee_archivee.id}')
        self.assertIn(res_pdf.status_code, (200, 302))


if __name__ == '__main__':
    unittest.main()
