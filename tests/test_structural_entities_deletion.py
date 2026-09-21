"""Tests de sécurité pour la suppression des entités structurelles (Classe, Professeur, Matière/Cours).

Couvre rigoureusement :
1. CLASSE :
   - Refus strict (HTTP 400) si la classe a des inscriptions (élèves).
   - Refus strict (HTTP 400) si la classe a des cours associés.
   - Succès de la suppression si la classe est totalement vide (0 élève, 0 cours).
   - Refus cross-tenant strict (HTTP 403) si un admin tente de supprimer une classe d'une autre école.

2. PROFESSEUR :
   - Refus strict (HTTP 400) si l'enseignant a des cours associés.
   - Refus strict (HTTP 400) si l'enseignant a des évaluations/notes.
   - Succès de la suppression si l'enseignant est vierge de tout enseignement.
   - Refus cross-tenant strict (HTTP 403).

3. MATIÈRE / COURS :
   - Refus strict (HTTP 400) si la matière a des évaluations/notes associées.
   - Succès de la suppression si la matière est vierge de toute évaluation.
   - Refus cross-tenant strict (HTTP 403).
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
    Note,
    Professeur,
    Utilisateur,
)


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-structural-entities-deletion"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class StructuralEntitiesDeletionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # 1. Deux écoles distinctes
        self.ecole_a = Ecole(nom="Ecole Alpha", adresse="Niamey", telephone="90000000", onboarding_complete=True)
        self.ecole_b = Ecole(nom="Ecole Beta", adresse="Maradi", telephone="91111111", onboarding_complete=True)
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # 2. Années scolaires actives
        self.annee_a = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_a, self.annee_b])
        db.session.flush()

        # 3. Administrateurs
        self.admin_a = Utilisateur(nom="Admin", prenom="Alpha", email="admin@alpha.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Admin", prenom="Beta", email="admin@beta.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin_a, self.admin_b])
        db.session.flush()

        # 4. Classes École A
        self.classe_avec_eleves = Classe(nom="6e A", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id)
        self.classe_avec_cours = Classe(nom="5e B", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id)
        self.classe_vide = Classe(nom="4e C (Erreur)", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id)
        # Classe École B
        self.classe_b = Classe(nom="6e Beta", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_avec_eleves, self.classe_avec_cours, self.classe_vide, self.classe_b])
        db.session.flush()

        # 5. Élève et Inscription dans classe_avec_eleves
        self.eleve = Eleve(nom="Koffi", prenom="Paul", date_naissance=date(2012, 5, 10), ecole_id=self.ecole_a.id)
        db.session.add(self.eleve)
        db.session.flush()

        self.ins = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe_avec_eleves.id,
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=120000,
        )
        db.session.add(self.ins)
        db.session.flush()

        # 6. Professeurs École A
        self.user_prof_cours = Utilisateur(nom="Diallo", prenom="Oumar", email="diallo@alpha.local", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id)
        self.user_prof_notes = Utilisateur(nom="Traore", prenom="Seydou", email="traore@alpha.local", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id)
        self.user_prof_vierge = Utilisateur(nom="Kone", prenom="Moussa", email="kone@alpha.local", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_a.id)
        self.user_prof_b = Utilisateur(nom="Sow", prenom="Ali", email="sow@beta.local", mot_de_passe="pass", role="professeur", ecole_id=self.ecole_b.id)
        db.session.add_all([self.user_prof_cours, self.user_prof_notes, self.user_prof_vierge, self.user_prof_b])
        db.session.flush()

        self.prof_cours = Professeur(nom="Diallo", prenom="Oumar", utilisateur_id=self.user_prof_cours.id, ecole_id=self.ecole_a.id)
        self.prof_notes = Professeur(nom="Traore", prenom="Seydou", utilisateur_id=self.user_prof_notes.id, ecole_id=self.ecole_a.id)
        self.prof_vierge = Professeur(nom="Kone", prenom="Moussa", utilisateur_id=self.user_prof_vierge.id, ecole_id=self.ecole_a.id)
        self.prof_b = Professeur(nom="Sow", prenom="Ali", utilisateur_id=self.user_prof_b.id, ecole_id=self.ecole_b.id)
        db.session.add_all([self.prof_cours, self.prof_notes, self.prof_vierge, self.prof_b])
        db.session.flush()

        # 7. Cours / Matières École A
        # Cours dans classe_avec_cours rattaché à prof_cours
        self.cours_classe = Cours(nom="Histoire-Géo", coefficient=2.0, ecole_id=self.ecole_a.id, classe_id=self.classe_avec_cours.id, professeur_id=self.prof_cours.id)
        # Cours rattaché à prof_notes avec une Note
        self.cours_avec_notes = Cours(nom="Mathématiques", coefficient=4.0, ecole_id=self.ecole_a.id, classe_id=self.classe_avec_eleves.id, professeur_id=self.prof_notes.id)
        # Cours vierge
        self.cours_vierge = Cours(nom="Dessin (Erreur)", coefficient=1.0, ecole_id=self.ecole_a.id, classe_id=self.classe_vide.id, professeur_id=None)
        # Cours École B
        self.cours_b = Cours(nom="Physique", coefficient=3.0, ecole_id=self.ecole_b.id, classe_id=self.classe_b.id, professeur_id=self.prof_b.id)
        db.session.add_all([self.cours_classe, self.cours_avec_notes, self.cours_vierge, self.cours_b])
        db.session.flush()

        # 8. Note pour le cours_avec_notes
        self.note = Note(
            valeur=16.5,
            coefficient=4.0,
            periode="Trimestre 1",
            cours_id=self.cours_avec_notes.id,
            eleve_id=self.eleve.id,
            inscription_id=self.ins.id,
            ecole_id=self.ecole_a.id,
            annee_id=self.annee_a.id,
        )
        db.session.add(self.note)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["ecole_id"] = user.ecole_id
            target_annee = self.annee_a if user.ecole_id == self.ecole_a.id else self.annee_b
            sess["annee_consultee"] = {str(user.ecole_id): target_annee.id}

    # ==================================================================
    # 1. TESTS CLASSE
    # ==================================================================

    def test_refus_suppression_classe_avec_eleves(self):
        """Une classe contenant des élèves inscrits ne peut être supprimée."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/classes/{self.classe_avec_eleves.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Impossible de supprimer une classe contenant des élèves ou des cours associés.", data["message"])

        # Vérifier que la classe est toujours en base
        self.assertIsNotNone(db.session.get(Classe, self.classe_avec_eleves.id))

    def test_refus_suppression_classe_avec_cours(self):
        """Une classe contenant des cours associés ne peut être supprimée."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/classes/{self.classe_avec_cours.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Impossible de supprimer une classe contenant des élèves ou des cours associés.", data["message"])

        # Vérifier que la classe est toujours en base
        self.assertIsNotNone(db.session.get(Classe, self.classe_avec_cours.id))

    def test_succes_suppression_classe_vide(self):
        """Une classe totalement vide (0 élève, 0 cours) peut être supprimée."""
        self._login(self.admin_a)

        # Retirer le cours_vierge de la classe_vide pour qu'elle soit à 0 cours et 0 élève
        db.session.delete(self.cours_vierge)
        db.session.commit()

        res = self.client.post(
            f"/classes/{self.classe_vide.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("supprimée avec succès", data["message"])

        # Vérifier que la classe a bien été supprimée
        self.assertIsNone(db.session.get(Classe, self.classe_vide.id))

    def test_refus_cross_tenant_classe(self):
        """Un admin de l'école A ne peut pas supprimer une classe de l'école B (403)."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/classes/{self.classe_b.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIsNotNone(db.session.get(Classe, self.classe_b.id))

    # ==================================================================
    # 2. TESTS PROFESSEUR / ENSEIGNANT
    # ==================================================================

    def test_refus_suppression_professeur_avec_cours(self):
        """Un professeur ayant des cours associés ne peut être supprimé."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/professeur/{self.prof_cours.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Cet enseignant possède un historique de cours ou d'évaluations.", data["message"])

        # Vérifier que le professeur existe toujours en base
        self.assertIsNotNone(db.session.get(Professeur, self.prof_cours.id))

    def test_refus_suppression_professeur_avec_notes(self):
        """Un professeur ayant des évaluations/notes ne peut être supprimé."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/professeur/{self.prof_notes.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Cet enseignant possède un historique de cours ou d'évaluations.", data["message"])

        self.assertIsNotNone(db.session.get(Professeur, self.prof_notes.id))

    def test_succes_suppression_professeur_vierge(self):
        """Un enseignant sans cours ni notes (compte vierge) peut être supprimé."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/professeur/{self.prof_vierge.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("supprimé avec succès", data["message"])

        # Vérifier suppression en base
        self.assertIsNone(db.session.get(Professeur, self.prof_vierge.id))

    def test_refus_cross_tenant_professeur(self):
        """Un admin de l'école A ne peut pas supprimer un professeur de l'école B (403)."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/professeur/{self.prof_b.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIsNotNone(db.session.get(Professeur, self.prof_b.id))

    # ==================================================================
    # 3. TESTS MATIÈRE / COURS
    # ==================================================================

    def test_refus_suppression_matiere_avec_notes(self):
        """Une matière/cours avec des notes enregistrées ne peut être supprimée."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/cours/{self.cours_avec_notes.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data["success"])
        self.assertIn("Impossible de supprimer cette matière", data["message"])

        self.assertIsNotNone(db.session.get(Cours, self.cours_avec_notes.id))

    def test_succes_suppression_matiere_vierge(self):
        """Une matière/cours vierge de toute évaluation peut être supprimée."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/cours/{self.cours_vierge.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("supprimé avec succès", data["message"])

        self.assertIsNone(db.session.get(Cours, self.cours_vierge.id))

    def test_refus_cross_tenant_matiere(self):
        """Un admin de l'école A ne peut pas supprimer une matière de l'école B (403)."""
        self._login(self.admin_a)

        res = self.client.post(
            f"/cours/{self.cours_b.id}/supprimer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIsNotNone(db.session.get(Cours, self.cours_b.id))

    def test_form_post_redirection_and_flash(self):
        """Vérifie le comportement d'une soumission formulaire standard (redirection avec message flash)."""
        self._login(self.admin_a)

        res = self.client.post(f"/classes/{self.classe_avec_eleves.id}/supprimer", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("Impossible de supprimer une classe contenant des élèves ou des cours associés.", html)


if __name__ == "__main__":
    unittest.main()

