"""tests/test_phase5c_notes_classe.py
==================================
Tests ciblés pour la Phase 5C :
Saisie rapide des notes par classe entière.

Scénarios couverts :
1. Admin ouvre la grille 6e A -> seuls les élèves de 6e A sont affichés.
2. Les élèves d'une autre classe ne sont pas dans la grille 6e A.
3. Les cours disponibles pour 6e A sont restreints à cette classe.
4. Saisie de 3 notes -> 3 enregistrements Note créés avec les bons attributs.
5. Champ vide pour un élève -> aucune note créée pour cet élève.
6. Tentative de falsification cours_id appartenant à une autre classe -> rejet.
7. Tentative de falsification eleve_id appartenant à une autre classe -> rejet.
8. Entité d'une autre école -> rejet strict.
9. Professeur assigné au cours -> autorisé à saisir les notes.
10. Professeur non assigné au cours -> rejet strict.
11. Année archivée -> POST rejeté.
12. Année planifiée -> POST rejeté avec message explicite.
13. Année active -> POST autorisé.
14. Les moyennes prennent en compte immédiatement les nouvelles notes.
15. Les bulletins prennent en compte immédiatement les nouvelles notes.
"""

import unittest
from datetime import date, datetime
from flask import session
from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Professeur,
    Utilisateur,
)
from app.services.niveaux import ensure_ecole_niveau_configs
from app.services.structure_annuelle import sauvegarder_structure_annee
from app.services.notes_annuelles import (
    MESSAGE_ANNEE_ARCHIVEE,
    MESSAGE_ANNEE_PLANIFIEE,
    calculer_moyennes_eleve_annee,
    calculer_statistiques_notes,
    get_classes_notes,
    get_cours_annee,
    get_inscriptions_notes,
    saisir_notes_classe,
)
from app.services.bulletins_annuels import calculer_bulletin_data


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase5CNotesClasseTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Écoles
        self.ecole_a = Ecole(nom="École A Test", statut="actif")
        self.ecole_b = Ecole(nom="École B Test", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Niveaux scolaires & pédagogie
        ensure_ecole_niveau_configs(self.ecole_a.id, commit=True)
        ensure_ecole_niveau_configs(self.ecole_b.id, commit=True)
        self.n6 = NiveauScolaire.query.filter_by(code="6E").first()
        self.n5 = NiveauScolaire.query.filter_by(code="5E").first()

        # Années scolaires École A
        self.archivee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        self.active = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.planifiee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )

        # Année scolaire École B
        self.annee_b = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.archivee, self.active, self.planifiee, self.annee_b])
        db.session.flush()

        sauvegarder_structure_annee(self.ecole_a.id, self.active.id, [self.n6.id, self.n5.id])
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b.id, [self.n6.id, self.n5.id])
        db.session.flush()

        # Classes École A (année active)
        self.classe_6a = Classe(
            nom="6ème A", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id
        )
        self.classe_5b = Classe(
            nom="5ème B", niveau="5e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id
        )

        # Classes École A (autres années)
        self.classe_archive = Classe(
            nom="6ème Archive", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.archivee.id
        )
        self.classe_plan = Classe(
            nom="6ème Plan", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.planifiee.id
        )

        # Classe École B
        self.classe_b = Classe(
            nom="6ème B-Ecole", niveau="6e", statut="ouverte", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id
        )
        db.session.add_all([self.classe_6a, self.classe_5b, self.classe_archive, self.classe_plan, self.classe_b])
        db.session.flush()

        # Utilisateurs
        self.admin = Utilisateur(
            nom="Admin", prenom="User", email="admin@ecole.com", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id
        )

        self.user_prof1 = Utilisateur(
            nom="Prof", prenom="Un", email="prof1@ecole.com", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id
        )

        self.user_prof2 = Utilisateur(
            nom="Prof", prenom="Deux", email="prof2@ecole.com", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id
        )

        self.admin_b = Utilisateur(
            nom="AdminB", prenom="User", email="admin@ecoleb.com", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id
        )

        db.session.add_all([self.admin, self.user_prof1, self.user_prof2, self.admin_b])
        db.session.flush()

        self.prof1 = Professeur(
            nom="Prof", prenom="Un", email="prof1@ecole.com", ecole_id=self.ecole_a.id, utilisateur_id=self.user_prof1.id
        )
        self.prof2 = Professeur(
            nom="Prof", prenom="Deux", email="prof2@ecole.com", ecole_id=self.ecole_a.id, utilisateur_id=self.user_prof2.id
        )
        db.session.add_all([self.prof1, self.prof2])
        db.session.flush()

        # Cours
        self.cours_math_6a = Cours(
            nom="Mathématiques 6A", classe_id=self.classe_6a.id, professeur_id=self.prof1.id, ecole_id=self.ecole_a.id
        )
        self.cours_francais_6a = Cours(
            nom="Français 6A", classe_id=self.classe_6a.id, professeur_id=self.prof2.id, ecole_id=self.ecole_a.id
        )
        self.cours_svt_5b = Cours(
            nom="SVT 5B", classe_id=self.classe_5b.id, professeur_id=self.prof2.id, ecole_id=self.ecole_a.id
        )
        self.cours_b = Cours(
            nom="Maths Ecole B", classe_id=self.classe_b.id, ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.cours_math_6a, self.cours_francais_6a, self.cours_svt_5b, self.cours_b])
        db.session.flush()

        # Élèves et Inscriptions
        # 3 élèves dans 6e A
        self.eleve_1 = Eleve(nom="DIALLO", prenom="Amadou", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_a.id)
        self.eleve_2 = Eleve(nom="BAH", prenom="Fatou", date_naissance=date(2013, 2, 2), ecole_id=self.ecole_a.id)
        self.eleve_3 = Eleve(nom="CAMARA", prenom="Ibrahim", date_naissance=date(2013, 3, 3), ecole_id=self.ecole_a.id)
        # 1 élève dans 5e B
        self.eleve_4 = Eleve(nom="SOW", prenom="Aissatou", date_naissance=date(2013, 4, 4), ecole_id=self.ecole_a.id)
        # 1 élève dans Ecole B
        self.eleve_b = Eleve(nom="TOURE", prenom="Moussa", date_naissance=date(2013, 5, 5), ecole_id=self.ecole_b.id)

        db.session.add_all([self.eleve_1, self.eleve_2, self.eleve_3, self.eleve_4, self.eleve_b])
        db.session.flush()

        self.ins_1 = Inscription(
            eleve_id=self.eleve_1.id, classe_id=self.classe_6a.id, annee_scolaire_id=self.active.id, ecole_id=self.ecole_a.id
        )
        self.ins_2 = Inscription(
            eleve_id=self.eleve_2.id, classe_id=self.classe_6a.id, annee_scolaire_id=self.active.id, ecole_id=self.ecole_a.id
        )
        self.ins_3 = Inscription(
            eleve_id=self.eleve_3.id, classe_id=self.classe_6a.id, annee_scolaire_id=self.active.id, ecole_id=self.ecole_a.id
        )
        self.ins_4 = Inscription(
            eleve_id=self.eleve_4.id, classe_id=self.classe_5b.id, annee_scolaire_id=self.active.id, ecole_id=self.ecole_a.id
        )
        self.ins_b = Inscription(
            eleve_id=self.eleve_b.id, classe_id=self.classe_b.id, annee_scolaire_id=self.annee_b.id, ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.ins_1, self.ins_2, self.ins_3, self.ins_4, self.ins_b])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user, annee_id=None):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            target_annee = annee_id or self.active.id
            if user.ecole_id:
                sess['annee_consultee'] = {str(user.ecole_id): target_annee}

    # 1. Admin ouvre la grille 6e A -> seuls les élèves de 6e A sont affichés
    def test_01_admin_opens_6a_grid_shows_only_6a_students(self):
        self._login(self.admin)
        resp = self.client.get(f"/notes/saisie_classeclasse_id={self.classe_6a.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("DIALLO", html)
        self.assertIn("BAH", html)
        self.assertIn("CAMARA", html)

    # 2. Les élèves d'une autre classe ne sont pas dans la grille 6e A
    def test_02_students_from_other_class_not_in_6a_grid(self):
        self._login(self.admin)
        resp = self.client.get(f"/notes/saisie_classeclasse_id={self.classe_6a.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn("SOW", html)
        self.assertNotIn("MAT004", html)

    # 3. Les cours disponibles pour 6e A sont restreints à cette classe
    def test_03_available_courses_in_6a_grid_only_6a_courses(self):
        self._login(self.admin)
        resp = self.client.get(f"/notes/saisie_classeclasse_id={self.classe_6a.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Mathématiques 6A", html)
        self.assertIn("Français 6A", html)
        # SVT 5B appartient à 5e B, ne doit pas être dans les options de cours
        self.assertNotIn("SVT 5B", html)

    # 4. Saisie de 3 notes -> 3 enregistrements Note créés avec les bons attributs
    def test_04_enter_3_grades_creates_3_notes_correct_attributes(self):
        self._login(self.admin)
        payload = {
            "classe_id": self.classe_6a.id,
            "cours_id": self.cours_math_6a.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 2.0,
            f"note_{self.eleve_1.id}": "15.5",
            f"note_{self.eleve_2.id}": "12.0",
            f"note_{self.eleve_3.id}": "18.25",
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        notes = Note.query.filter_by(cours_id=self.cours_math_6a.id).all()
        self.assertEqual(len(notes), 3)

        notes_dict = {n.eleve_id: n for n in notes}
        self.assertIn(self.eleve_1.id, notes_dict)
        self.assertEqual(notes_dict[self.eleve_1.id].valeur, 15.5)
        self.assertEqual(notes_dict[self.eleve_1.id].coefficient, 2.0)
        self.assertEqual(notes_dict[self.eleve_1.id].periode, "Semestre 1")
        self.assertEqual(notes_dict[self.eleve_1.id].type_evaluation, "Devoir")
        self.assertEqual(notes_dict[self.eleve_1.id].annee_id, self.active.id)
        self.assertEqual(notes_dict[self.eleve_1.id].inscription_id, self.ins_1.id)
        self.assertEqual(notes_dict[self.eleve_1.id].ecole_id, self.ecole_a.id)

    # 5. Champ vide pour un élève -> aucune note créée pour cet élève
    def test_05_empty_field_no_note_created(self):
        self._login(self.admin)
        payload = {
            "classe_id": self.classe_6a.id,
            "cours_id": self.cours_math_6a.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.0,
            f"note_{self.eleve_1.id}": "14.0",
            f"note_{self.eleve_2.id}": "",    # vide
            f"note_{self.eleve_3.id}": "   ", # espaces
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        notes = Note.query.filter_by(cours_id=self.cours_math_6a.id).all()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].eleve_id, self.eleve_1.id)
        self.assertEqual(notes[0].valeur, 14.0)

    # 6. Tentative de falsification cours_id appartenant à une autre classe -> rejet
    def test_06_forged_course_id_of_another_class_rejected(self):
        self._login(self.admin)
        payload = {
            "classe_id": self.classe_6a.id,
            "cours_id": self.cours_svt_5b.id,  # cours de 5e B forgé !
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.0,
            f"note_{self.eleve_1.id}": "16.0",
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        # Aucune note ne doit être créée
        notes = Note.query.filter_by(cours_id=self.cours_svt_5b.id).all()
        self.assertEqual(len(notes), 0)

    # 7. Tentative de falsification eleve_id appartenant à une autre classe -> rejet
    def test_07_forged_student_id_of_another_class_rejected(self):
        self._login(self.admin)
        payload = {
            "classe_id": self.classe_6a.id,
            "cours_id": self.cours_math_6a.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.0,
            f"note_{self.eleve_4.id}": "15.0",  # eleve_4 est en 5e B, pas en 6e A !
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        # Aucune note créée
        notes = Note.query.filter_by(cours_id=self.cours_math_6a.id).all()
        self.assertEqual(len(notes), 0)

    # 8. Entité d'une autre école -> rejet strict
    def test_08_other_school_entity_rejected(self):
        self._login(self.admin)
        payload = {
            "classe_id": self.classe_b.id,     # classe de l'École B !
            "cours_id": self.cours_b.id,      # cours de l'École B !
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.0,
            f"note_{self.eleve_b.id}": "15.0",
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        # Aucune note créée
        notes = Note.query.filter_by(cours_id=self.cours_b.id).all()
        self.assertEqual(len(notes), 0)

    # 9. Professeur assigné au cours -> autorisé à saisir les notes
    def test_09_teacher_assigned_to_course_authorized(self):
        self._login(self.user_prof1)  # prof1 assigné à Mathématiques 6A
        payload = {
            "classe_id": self.classe_6a.id,
            "cours_id": self.cours_math_6a.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.5,
            f"note_{self.eleve_1.id}": "14.5",
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        notes = Note.query.filter_by(cours_id=self.cours_math_6a.id).all()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].valeur, 14.5)
        self.assertEqual(notes[0].last_by_admin, False)

    # 10. Professeur non assigné au cours -> rejet strict
    def test_10_teacher_not_assigned_to_course_rejected(self):
        self._login(self.user_prof1)  # prof1 N'EST PAS assigné à Français 6A (c'est prof2)
        payload = {
            "classe_id": self.classe_6a.id,
            "cours_id": self.cours_francais_6a.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.0,
            f"note_{self.eleve_1.id}": "17.0",
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        notes = Note.query.filter_by(cours_id=self.cours_francais_6a.id).all()
        self.assertEqual(len(notes), 0)

    # 11. Année archivée -> POST rejeté
    def test_11_archived_year_post_rejected(self):
        # On définit l'année consultée comme archivée dans la session
        self._login(self.admin, annee_id=self.archivee.id)

        nb, err = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            user=self.admin,
            classe_id=self.classe_archive.id,
            cours_id=self.cours_math_6a.id,
            notes_dict={self.eleve_1.id: 15.0},
            periode="Semestre 1",
        )
        self.assertEqual(nb, 0)
        self.assertEqual(err, MESSAGE_ANNEE_ARCHIVEE)

        # Vérification via route HTTP
        payload = {
            "classe_id": self.classe_archive.id,
            "cours_id": self.cours_math_6a.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.0,
            f"note_{self.eleve_1.id}": "15.0",
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("archiv", html.lower())

    # 12. Année planifiée -> POST rejeté avec message explicite
    def test_12_planned_year_post_rejected(self):
        self._login(self.admin, annee_id=self.planifiee.id)

        nb, err = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            classe_id=self.classe_plan.id,
            cours_id=self.cours_math_6a.id,
            notes_dict={self.eleve_1.id: 15.0},
            periode="Semestre 1",
        )
        self.assertEqual(nb, 0)
        self.assertEqual(err, MESSAGE_ANNEE_PLANIFIEE)

        payload = {
            "classe_id": self.classe_plan.id,
            "cours_id": self.cours_math_6a.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.0,
            f"note_{self.eleve_1.id}": "15.0",
        }
        resp = self.client.post("/notes/saisie_classe", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("active", html.lower())

    # 13. Année active -> POST autorisé
    def test_13_active_year_authorized(self):
        self._login(self.admin)
        with self.client.session_transaction() as sess:
            sess["annee_consultee"] = self.active.id

        nb, err = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            classe_id=self.classe_6a.id,
            cours_id=self.cours_math_6a.id,
            notes_dict={self.eleve_1.id: 16.0},
        )
        self.assertEqual(nb, 1)
        self.assertIsNone(err)

    # 14. Les moyennes prennent en compte immédiatement les nouvelles notes
    def test_14_averages_factor_new_notes_immediately(self):
        # Saisir 2 notes pour eleve_1 : 12 (coef 1) et 16 (coef 3) -> moyenne attendue : (12*1 + 16*3)/4 = 60/4 = 15.0
        nb1, err1 = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            classe_id=self.classe_6a.id,
            cours_id=self.cours_math_6a.id,
            notes_dict={self.eleve_1.id: 12.0},
            coefficient=1.0,
        )
        self.assertEqual(nb1, 1)
        self.assertIsNone(err1)

        nb2, err2 = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            classe_id=self.classe_6a.id,
            cours_id=self.cours_math_6a.id,
            notes_dict={self.eleve_1.id: 16.0},
            coefficient=3.0,
        )
        self.assertEqual(nb2, 1)
        self.assertIsNone(err2)

        moyennes = calculer_moyennes_eleve_annee(
            inscription_id=self.ins_1.id,
            ecole_id=self.ecole_a.id,
            annee_id=self.active.id,
        )
        self.assertEqual(moyennes["moyenne"], 15.0)
        self.assertEqual(moyennes["total_coefficients"], 4.0)
        self.assertIn(self.cours_math_6a.id, moyennes["par_matiere"])
        self.assertEqual(moyennes["par_matiere"][self.cours_math_6a.id]["moyenne"], 15.0)

    # 15. Les bulletins prennent en compte immédiatement les nouvelles notes
    def test_15_bulletins_factor_new_notes_immediately(self):
        # Saisir un devoir et une composition via la saisie par classe
        nb, err = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            classe_id=self.classe_6a.id,
            cours_id=self.cours_math_6a.id,
            notes_dict={self.eleve_1.id: 18.0},
            coefficient=2.0,
            periode="Semestre 1",
            type_evaluation="Devoir",
        )
        self.assertEqual(nb, 1)
        self.assertIsNone(err)

        nb_comp, err_comp = saisir_notes_classe(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            classe_id=self.classe_6a.id,
            cours_id=self.cours_math_6a.id,
            notes_dict={self.eleve_1.id: 18.0},
            coefficient=2.0,
            periode="Semestre 1",
            type_evaluation="Composition",
        )
        self.assertEqual(nb_comp, 1)
        self.assertIsNone(err_comp)

        # Calcul des données de bulletin pour l'inscription 1
        bulletin_data, b_err = calculer_bulletin_data(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            inscription=self.ins_1,
            periode="Semestre 1",
        )
        self.assertIsNone(b_err)
        self.assertIsNotNone(bulletin_data)
        self.assertIn("Mathématiques 6A", bulletin_data["moyennes_par_cours"])
        self.assertEqual(bulletin_data["moyennes_par_cours"]["Mathématiques 6A"], 18.0)
        self.assertEqual(bulletin_data["moyenne_generale"], 18.0)
