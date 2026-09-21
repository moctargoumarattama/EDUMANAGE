"""Tests pour l'annulation douce des paiements (statut = 'annule').

Couvre impérativement :
a. L'annulation d'un paiement passe son statut à 'annule' sans supprimer la ligne en base de données.
b. Les paiements au statut 'annule' ne sont plus comptabilisés dans le total payé de l'élève (le solde dû de l'élève se rétablit en conséquence).
c. Refus de double annulation si le paiement est déjà au statut 'annule'.
d. Refus cross-tenant strict (un admin de l'école A ne peut pas annuler un paiement de l'école B -> 403).
"""

from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Paiement, Utilisateur
from app.services.paiements_annuels import get_finances_inscription


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-payment-cancellation"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class PaymentCancellationTestCase(unittest.TestCase):
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

        # Admins
        self.admin_a = Utilisateur(nom="Admin A", email="admin-a@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Admin B", email="admin-b@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin_a, self.admin_b])
        db.session.flush()

        # Élèves et Inscriptions
        self.eleve_a = Eleve(nom="Diallo", prenom="Mamadou", date_naissance=date(2012, 5, 10), frais_annuels=100000, ecole_id=self.ecole_a.id)
        self.eleve_b = Eleve(nom="Sow", prenom="Fatou", date_naissance=date(2013, 1, 1), frais_annuels=100000, ecole_id=self.ecole_b.id)
        db.session.add_all([self.eleve_a, self.eleve_b])
        db.session.flush()

        self.ins_a = Inscription(
            eleve_id=self.eleve_a.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id,
            ecole_id=self.ecole_a.id,
            frais_annuels=100000,
        )
        self.ins_b = Inscription(
            eleve_id=self.eleve_b.id,
            classe_id=self.classe_b.id,
            annee_scolaire_id=self.annee_b.id,
            ecole_id=self.ecole_b.id,
            frais_annuels=100000,
        )
        db.session.add_all([self.ins_a, self.ins_b])
        db.session.flush()

        # Paiements
        self.paiement_a = Paiement(
            montant=25000,
            mois="Octobre",
            annee=2026,
            mode_paiement="Espèces",
            statut="payé",
            eleve_id=self.eleve_a.id,
            inscription_id=self.ins_a.id,
            ecole_id=self.ecole_a.id,
            date_paiement=datetime(2026, 10, 5, 9, 30),
        )
        self.paiement_b = Paiement(
            montant=30000,
            mois="Octobre",
            annee=2026,
            mode_paiement="Virement",
            statut="payé",
            eleve_id=self.eleve_b.id,
            inscription_id=self.ins_b.id,
            ecole_id=self.ecole_b.id,
            date_paiement=datetime(2026, 10, 6, 10, 0),
        )
        db.session.add_all([self.paiement_a, self.paiement_b])
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

    # ------------------------------------------------------------------
    # a. L'annulation passe le statut à 'annule' sans suppression physique
    # ------------------------------------------------------------------
    def test_cancellation_marks_status_annule_without_db_deletion(self):
        self._login(self.admin_a)

        # Appel POST d'annulation avec motif
        res = self.client.post(
            f"/paiement/{self.paiement_a.id}/supprimer",
            data={"motif": "Erreur de saisie caisse"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIn("annulé", data["message"])

        # Vérification en base : le paiement existe toujours !
        p_db = db.session.get(Paiement, self.paiement_a.id)
        self.assertIsNotNone(p_db)
        self.assertEqual(p_db.statut, "annule")
        self.assertEqual(p_db.montant, 25000)

    # ------------------------------------------------------------------
    # b. Les paiements 'annule' sont exclus du total payé (solde rétabli)
    # ------------------------------------------------------------------
    def test_cancelled_payments_excluded_from_total_and_balance_restored(self):
        self._login(self.admin_a)

        # Avant annulation : 25 000 FCFA payés, reste = 75 000 FCFA
        fin_avant = get_finances_inscription(self.ins_a)
        self.assertEqual(fin_avant["total_paye"], 25000.0)
        self.assertEqual(fin_avant["reste_a_payer"], 75000.0)
        self.assertEqual(self.eleve_a.total_paye(), 25000.0)
        self.assertEqual(self.eleve_a.reste_a_payer(), 75000.0)

        # Annulation du paiement
        res = self.client.post(f"/paiement/{self.paiement_a.id}/annuler", follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Rafraîchir les objets de session
        db.session.refresh(self.ins_a)
        db.session.refresh(self.eleve_a)

        # Après annulation : total payé retombe à 0, le solde dû est de 100 000 FCFA
        fin_apres = get_finances_inscription(self.ins_a)
        self.assertEqual(fin_apres["total_paye"], 0.0)
        self.assertEqual(fin_apres["reste_a_payer"], 100000.0)
        self.assertEqual(fin_apres["statut_solde"], "aucun")

        # Vérification méthode modèle
        self.assertEqual(self.eleve_a.total_paye(), 0.0)
        self.assertEqual(self.eleve_a.reste_a_payer(), 100000.0)

    # ------------------------------------------------------------------
    # c. Refus de double annulation
    # ------------------------------------------------------------------
    def test_refusal_of_double_cancellation(self):
        self._login(self.admin_a)

        # 1ère annulation
        res1 = self.client.post(
            f"/paiement/{self.paiement_a.id}/annuler",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res1.status_code, 200)
        self.assertTrue(res1.get_json()["success"])

        # 2ème tentative d'annulation
        res2 = self.client.post(
            f"/paiement/{self.paiement_a.id}/annuler",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res2.status_code, 400)
        json_data = res2.get_json()
        self.assertFalse(json_data["success"])
        self.assertIn("déjà été annulé", json_data["message"])

        # Test formulaire standard 2ème tentative
        res3 = self.client.post(f"/paiement/{self.paiement_a.id}/annuler", follow_redirects=True)
        self.assertEqual(res3.status_code, 200)
        self.assertIn("déjà été annulé", res3.get_data(as_text=True))

    # ------------------------------------------------------------------
    # d. Refus cross-tenant strict (Admin A ne peut pas annuler paiement B)
    # ------------------------------------------------------------------
    def test_cross_tenant_cancellation_refused(self):
        self._login(self.admin_a)

        # Admin A tente d'annuler paiement_b qui appartient à ecole_b
        res_ajax = self.client.post(
            f"/paiement/{self.paiement_b.id}/annuler",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(res_ajax.status_code, 403)

        res_form = self.client.post(f"/paiement/{self.paiement_b.id}/annuler")
        self.assertEqual(res_form.status_code, 403)

        # Vérification en base : le paiement B est resté intact
        p_b_db = db.session.get(Paiement, self.paiement_b.id)
        self.assertIsNotNone(p_b_db)
        self.assertEqual(p_b_db.statut, "payé")


if __name__ == "__main__":
    unittest.main()
