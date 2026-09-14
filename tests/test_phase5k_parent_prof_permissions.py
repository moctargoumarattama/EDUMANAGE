"""
Tests KLASORA — Phase 5K : Audit complet permissions PROFESSEUR et PARENT.
10 tests unitaires et d'intégration ciblés.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Bulletin,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Paiement,
    PeriodeBulletin,
    Professeur,
    Utilisateur,
    professeur_classes,
)


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5k"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestPhase5KPermissions(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # École 1
        self.ecole1 = Ecole(nom="Ecole Alpha")
        db.session.add(self.ecole1)
        db.session.flush()

        # École 2 (pour test inter-école)
        self.ecole2 = Ecole(nom="Ecole Beta")
        db.session.add(self.ecole2)
        db.session.flush()

        # Année scolaire active Ecole 1
        self.annee1 = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="active",
            ecole_id=self.ecole1.id,
        )
        db.session.add(self.annee1)
        db.session.flush()

        # Niveau & Classes
        self.niveau = NiveauScolaire(code="6EME", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niveau)
        db.session.flush()

        # Utilisateurs & Professeurs
        self.user_prof_a = Utilisateur(nom="ProfA", prenom="Jean", email="profa@test.com", mot_de_passe="pass", role="professeur", ecole_id=self.ecole1.id)
        self.user_prof_b = Utilisateur(nom="ProfB", prenom="Paul", email="profb@test.com", mot_de_passe="pass", role="professeur", ecole_id=self.ecole1.id)
        self.user_parent_a = Utilisateur(nom="ParentA", prenom="Alice", email="parenta@test.com", mot_de_passe="pass", role="parent", ecole_id=self.ecole1.id)
        self.user_parent_b = Utilisateur(nom="ParentB", prenom="Bob", email="parentb@test.com", mot_de_passe="pass", role="parent", ecole_id=self.ecole1.id)
        self.user_admin = Utilisateur(nom="Admin", prenom="Directeur", email="admin@test.com", mot_de_passe="pass", role="admin", ecole_id=self.ecole1.id)
        db.session.add_all([self.user_prof_a, self.user_prof_b, self.user_parent_a, self.user_parent_b, self.user_admin])
        db.session.flush()

        self.prof_a = Professeur(nom="ProfA", prenom="Jean", utilisateur_id=self.user_prof_a.id, ecole_id=self.ecole1.id)
        self.prof_b = Professeur(nom="ProfB", prenom="Paul", utilisateur_id=self.user_prof_b.id, ecole_id=self.ecole1.id)
        db.session.add_all([self.prof_a, self.prof_b])
        db.session.flush()

        self.classe_a = Classe(nom="6ème A", ecole_id=self.ecole1.id, annee_scolaire_id=self.annee1.id, niveau_id=self.niveau.id, professeur_id=self.prof_a.id)
        self.classe_b = Classe(nom="6ème B", ecole_id=self.ecole1.id, annee_scolaire_id=self.annee1.id, niveau_id=self.niveau.id, professeur_id=self.prof_b.id)
        db.session.add_all([self.classe_a, self.classe_b])
        db.session.flush()

        # Association professeur-classe dans table de liaison
        db.session.execute(professeur_classes.insert().values(professeur_id=self.prof_a.id, classe_id=self.classe_a.id, ecole_id=self.ecole1.id))
        db.session.execute(professeur_classes.insert().values(professeur_id=self.prof_b.id, classe_id=self.classe_b.id, ecole_id=self.ecole1.id))

        # Cours
        self.cours_a = Cours(nom="Maths 6A", ecole_id=self.ecole1.id, classe_id=self.classe_a.id, professeur_id=self.prof_a.id)
        self.cours_b = Cours(nom="Francais 6B", ecole_id=self.ecole1.id, classe_id=self.classe_b.id, professeur_id=self.prof_b.id)
        db.session.add_all([self.cours_a, self.cours_b])
        db.session.flush()

        # Élèves
        self.eleve_a = Eleve(nom="Diallo", prenom="Mamadou", date_naissance=date(2012, 5, 10), ecole_id=self.ecole1.id, classe_id=self.classe_a.id, parent_id=self.user_parent_a.id, frais_annuels=100000.0)
        self.eleve_b = Eleve(nom="Sow", prenom="Fatou", date_naissance=date(2012, 8, 15), ecole_id=self.ecole1.id, classe_id=self.classe_b.id, parent_id=self.user_parent_b.id, frais_annuels=100000.0)
        db.session.add_all([self.eleve_a, self.eleve_b])
        db.session.flush()

        # Inscriptions
        self.ins_a = Inscription(eleve_id=self.eleve_a.id, classe_id=self.classe_a.id, annee_scolaire_id=self.annee1.id, ecole_id=self.ecole1.id)
        self.ins_b = Inscription(eleve_id=self.eleve_b.id, classe_id=self.classe_b.id, annee_scolaire_id=self.annee1.id, ecole_id=self.ecole1.id)
        db.session.add_all([self.ins_a, self.ins_b])
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
            sess['annee_consultee_id'] = self.annee1.id

    # 1. Prof A access assigned course/class
    def test_01_prof_access_assigned_course_and_class(self):
        self._login("profa@test.com")
        res = self.client.get(f"/api/eleves/classe/{self.classe_a.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(len(data.get('eleves', [])) > 0)
        self.assertEqual(data['eleves'][0]['id'], self.eleve_a.id)

    # 2. Prof A forbidden from unassigned class B
    def test_02_prof_forbidden_unassigned_course_and_class(self):
        self._login("profa@test.com")
        res = self.client.get(f"/api/eleves/classe/{self.classe_b.id}")
        self.assertEqual(res.status_code, 403)

    # 3. Prof A forged POST grade on Prof B's course
    def test_03_prof_forged_post_grade_unauthorized_course(self):
        self._login("profa@test.com")
        res = self.client.post("/notes", data={
            "eleve_id": self.eleve_b.id,
            "cours_id": self.cours_b.id,
            "valeur": 15.0,
            "coefficient": 1.0,
            "type_evaluation": "Devoir",
            "periode": "Semestre 1"
        }, follow_redirects=True)
        # Verify no note was created for cours_b
        note = Note.query.filter_by(cours_id=self.cours_b.id, eleve_id=self.eleve_b.id).first()
        self.assertIsNone(note)

    # 4. Prof A forged POST absence for unassigned student
    def test_04_prof_forged_post_absence_unassigned_student(self):
        self._login("profa@test.com")
        res = self.client.post("/absences", data={
            "eleve_id": self.eleve_b.id,
            "cours_id": self.cours_b.id,
            "date_absence": "2024-10-15",
            "motif": "Non autorise",
            "justifiee": False
        }, follow_redirects=True)
        # Should flash danger error and not create absence
        from app.models import Absence
        abs_rec = Absence.query.filter_by(eleve_id=self.eleve_b.id).first()
        self.assertIsNone(abs_rec)

    # 5. Prof forbidden from financial routes & dossier financial info
    def test_05_prof_forbidden_financial_access(self):
        self._login("profa@test.com")
        # Direct payments route must return 403
        res = self.client.get("/paiements")
        self.assertEqual(res.status_code, 403)

        # Student dossier accessible (200) for assigned class student
        res_dossier = self.client.get(f"/voir_eleve/{self.eleve_a.id}")
        self.assertEqual(res_dossier.status_code, 200)

    # 6. Prof forbidden from school structure modifications
    def test_06_prof_forbidden_school_structure_modifications(self):
        self._login("profa@test.com")
        res = self.client.get("/creer_periode")
        self.assertEqual(res.status_code, 403)

        res_annee = self.client.get("/periodes")
        self.assertEqual(res_annee.status_code, 403)

    # 7. Parent A access own child
    def test_07_parent_access_own_child(self):
        self._login("parenta@test.com")
        res = self.client.get(f"/voir_eleve/{self.eleve_a.id}")
        self.assertEqual(res.status_code, 200)

    # 8. Parent A forged ID child of Parent B -> 403 or redirect
    def test_08_parent_forged_access_other_parent_child(self):
        self._login("parenta@test.com")
        res = self.client.get(f"/voir_eleve/{self.eleve_b.id}")
        self.assertIn(res.status_code, [403, 302])

    # 9. Parent A forbidden from unpublished bulletin
    def test_09_parent_forbidden_unpublished_bulletin(self):
        self._login("parenta@test.com")
        res = self.client.get(f"/bulletin_eleve/{self.eleve_a.id}", follow_redirects=True)
        # Should redirect back to parent_dashboard because period is not published
        self.assertIn("bulletins ne sont pas encore disponibles", res.get_data(as_text=True))

    # 10. Parent forbidden from administrative mutations
    def test_10_parent_forbidden_administrative_mutations(self):
        self._login("parenta@test.com")
        res_note = self.client.post("/notes", data={
            "eleve_id": self.eleve_a.id,
            "cours_id": self.cours_a.id,
            "valeur": 18.0
        })
        self.assertIn(res_note.status_code, [403, 302])

        res_eleve = self.client.post(f"/eleve/{self.eleve_a.id}/modifier", data={"nom": "Hack"})
        self.assertIn(res_eleve.status_code, [403, 302])


if __name__ == "__main__":
    unittest.main()
