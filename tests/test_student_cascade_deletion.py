"""Tests de sécurité pour la suppression sécurisée d'un élève (supprimer_eleve_cascade).

Couvre impérativement :
a. Refus de suppression si l'élève a au moins 1 note (données préservées).
b. Refus de suppression si l'élève a au moins 1 paiement (données préservées).
c. Refus de suppression si l'élève a au moins 1 absence (données préservées).
d. Succès de la suppression si l'élève n'a aucun historique (0 note, 0 paiement, 0 absence).
e. Refus cross-tenant (un admin de l'école A ne peut pas supprimer un élève de l'école B).
"""

from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import Absence, AnneeScolaire, Classe, Cours, Ecole, Eleve, Inscription, Note, Paiement, Utilisateur


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-student-cascade-deletion"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class StudentCascadeDeletionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Écoles
        self.ecole_a = Ecole(nom="Ecole Alpha", adresse="Niamey", telephone="90000000", onboarding_complete=True)
        self.ecole_b = Ecole(nom="Ecole Beta", adresse="Maradi", telephone="91111111", onboarding_complete=True)
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Années scolaires
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

        # Classes
        self.classe_a = Classe(nom="6e A", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id)
        self.classe_b = Classe(nom="6e B", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_a, self.classe_b])
        db.session.flush()

        # Cours
        self.cours_a = Cours(nom="Mathématiques", ecole_id=self.ecole_a.id, classe_id=self.classe_a.id)
        db.session.add(self.cours_a)
        db.session.flush()

        # Admins
        self.admin_a = Utilisateur(nom="Admin A", email="admin-a@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Admin B", email="admin-b@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin_a, self.admin_b])
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
            sess["annee_consultee_id"] = self.annee_a.id if user.ecole_id == self.ecole_a.id else self.annee_b.id

    def _creer_eleve(self, ecole, classe, annee, nom="Diallo", prenom="Mamadou"):
        eleve = Eleve(nom=nom, prenom=prenom, date_naissance=date(2012, 5, 10), ecole_id=ecole.id)
        db.session.add(eleve)
        db.session.flush()

        inscription = Inscription(
            eleve_id=eleve.id,
            classe_id=classe.id,
            annee_scolaire_id=annee.id,
            ecole_id=ecole.id,
            frais_annuels=100000,
        )
        db.session.add(inscription)
        db.session.commit()
        return eleve, inscription

    # ------------------------------------------------------------------
    # a. Refus de suppression si l'élève a au moins 1 note
    # ------------------------------------------------------------------
    def test_rejection_when_student_has_note(self):
        self._login(self.admin_a)
        eleve, ins = self._creer_eleve(self.ecole_a, self.classe_a, self.annee_a, nom="NoteTest", prenom="Jean")

        note = Note(
            valeur=15.0,
            type_evaluation="devoir",
            periode="Semestre 1",
            eleve_id=eleve.id,
            cours_id=self.cours_a.id,
            ecole_id=self.ecole_a.id,
            inscription_id=ins.id,
        )
        db.session.add(note)
        db.session.commit()

        # Test requête AJAX -> 400
        res_ajax = self.client.post(
            f"/eleve/{eleve.id}/supprimer-cascade",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res_ajax.status_code, 400)
        json_data = res_ajax.get_json()
        self.assertFalse(json_data["success"])
        self.assertIn("historique scolaire ou comptable", json_data["message"])

        # Test requête formulaire standard -> redirection avec message flash
        res_form = self.client.post(f"/eleve/{eleve.id}/supprimer-cascade", follow_redirects=True)
        self.assertEqual(res_form.status_code, 200)
        self.assertIn("Impossible de supprimer un", res_form.get_data(as_text=True))

        # Vérifier que les données sont intactes en base
        self.assertIsNotNone(db.session.get(Eleve, eleve.id))
        self.assertIsNotNone(db.session.get(Inscription, ins.id))
        self.assertIsNotNone(db.session.get(Note, note.id))

    # ------------------------------------------------------------------
    # b. Refus de suppression si l'élève a au moins 1 paiement
    # ------------------------------------------------------------------
    def test_rejection_when_student_has_payment(self):
        self._login(self.admin_a)
        eleve, ins = self._creer_eleve(self.ecole_a, self.classe_a, self.annee_a, nom="PayTest", prenom="Paul")

        paiement = Paiement(
            montant=25000,
            mois="Octobre",
            annee=2026,
            mode_paiement="Espèces",
            statut="payé",
            eleve_id=eleve.id,
            inscription_id=ins.id,
            ecole_id=self.ecole_a.id,
            date_paiement=datetime(2026, 10, 5, 9, 30),
        )
        db.session.add(paiement)
        db.session.commit()

        # Requête AJAX -> 400
        res_ajax = self.client.post(
            f"/eleve/{eleve.id}/supprimer-cascade",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res_ajax.status_code, 400)
        json_data = res_ajax.get_json()
        self.assertFalse(json_data["success"])
        self.assertIn("historique scolaire ou comptable", json_data["message"])

        # Vérifier que rien n'a été supprimé
        self.assertIsNotNone(db.session.get(Eleve, eleve.id))
        self.assertIsNotNone(db.session.get(Inscription, ins.id))
        self.assertIsNotNone(db.session.get(Paiement, paiement.id))

    # ------------------------------------------------------------------
    # c. Refus de suppression si l'élève a au moins 1 absence
    # ------------------------------------------------------------------
    def test_rejection_when_student_has_absence(self):
        self._login(self.admin_a)
        eleve, ins = self._creer_eleve(self.ecole_a, self.classe_a, self.annee_a, nom="AbsTest", prenom="Ali")

        absence = Absence(
            date_absence=datetime(2026, 10, 10, 8, 0),
            motif="Maladie",
            justifiee=True,
            eleve_id=eleve.id,
            cours_id=self.cours_a.id,
            ecole_id=self.ecole_a.id,
            inscription_id=ins.id,
        )
        db.session.add(absence)
        db.session.commit()

        # Requête AJAX -> 400
        res_ajax = self.client.post(
            f"/eleve/{eleve.id}/supprimer-cascade",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res_ajax.status_code, 400)
        json_data = res_ajax.get_json()
        self.assertFalse(json_data["success"])
        self.assertIn("historique scolaire ou comptable", json_data["message"])

        # Vérifier que rien n'a été supprimé
        self.assertIsNotNone(db.session.get(Eleve, eleve.id))
        self.assertIsNotNone(db.session.get(Inscription, ins.id))
        self.assertIsNotNone(db.session.get(Absence, absence.id))

    # ------------------------------------------------------------------
    # d. Succès de la suppression si l'élève n'a aucun historique
    # ------------------------------------------------------------------
    def test_success_when_student_has_no_history(self):
        self._login(self.admin_a)
        eleve, ins = self._creer_eleve(self.ecole_a, self.classe_a, self.annee_a, nom="Coquille", prenom="Vide")
        eleve_id = eleve.id
        ins_id = ins.id

        # Requête AJAX -> 200
        res = self.client.post(
            f"/eleve/{eleve_id}/supprimer-cascade",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertTrue(json_data["success"])
        self.assertIn("créé par erreur supprimé avec succès", json_data["message"])

        # L'élève et ses inscriptions associées doivent avoir disparu
        self.assertIsNone(db.session.get(Eleve, eleve_id))
        self.assertIsNone(db.session.get(Inscription, ins_id))

    def test_success_form_post_when_student_has_no_history(self):
        self._login(self.admin_a)
        eleve, ins = self._creer_eleve(self.ecole_a, self.classe_a, self.annee_a, nom="ErreurSaisie", prenom="Luc")
        eleve_id = eleve.id
        ins_id = ins.id

        # Requête formulaire standard -> redirection
        res = self.client.post(f"/eleve/{eleve_id}/supprimer-cascade", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Élève créé par erreur supprimé avec succès", res.get_data(as_text=True))

        # Vérification en base
        self.assertIsNone(db.session.get(Eleve, eleve_id))
        self.assertIsNone(db.session.get(Inscription, ins_id))

    # ------------------------------------------------------------------
    # e. Refus cross-tenant (admin école A ne peut pas supprimer élève école B)
    # ------------------------------------------------------------------
    def test_cross_tenant_rejection(self):
        # Admin A tente de supprimer un élève de l'école B
        self._login(self.admin_a)
        eleve_b, ins_b = self._creer_eleve(self.ecole_b, self.classe_b, self.annee_b, nom="Autre", prenom="Ecole")

        res = self.client.post(
            f"/eleve/{eleve_b.id}/supprimer-cascade",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 403)

        # Vérifier que l'élève de l'école B n'a absolument pas été supprimé
        self.assertIsNotNone(db.session.get(Eleve, eleve_b.id))
        self.assertIsNotNone(db.session.get(Inscription, ins_b.id))


if __name__ == "__main__":
    unittest.main()

