from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Paiement, Utilisateur
from app.services.payment_receipts import build_payment_receipt_context


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-payment-receipt"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class PaymentReceiptCanonicalTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole Alpha", adresse="Niamey", telephone="90000000", email="contact@alpha.test", onboarding_complete=True)
        self.ecole_b = Ecole(nom="Ecole Beta", adresse="Maradi", telephone="91111111", onboarding_complete=True)
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

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

        self.classe_a = Classe(nom="6e A", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id)
        self.classe_b = Classe(nom="6e B", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_a, self.classe_b])
        db.session.flush()

        self.admin_a = Utilisateur(nom="Admin A", email="admin-a@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_a.id)
        self.admin_b = Utilisateur(nom="Admin B", email="admin-b@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin_a, self.admin_b])
        db.session.flush()

        self.eleve_a = Eleve(
            nom="Diallo",
            prenom="Mamadou",
            date_naissance=date(2012, 5, 10),
            telephone="92222222",
            email_parent="parent@example.test",
            ecole_id=self.ecole_a.id,
        )
        self.eleve_b = Eleve(nom="Sow", prenom="Fatou", date_naissance=date(2013, 1, 1), ecole_id=self.ecole_b.id)
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

        self.paiement_a = Paiement(
            montant=25000,
            mois="Octobre",
            annee=2026,
            mode_paiement="Espèces",
            statut="payé",
            reference="REF-SECRET-001",
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

    def test_private_receipt_authorized_and_generates_stable_token(self):
        self._login(self.admin_a)
        res1 = self.client.get(f"/paiements/{self.paiement_a.id}")
        self.assertEqual(res1.status_code, 200)
        html = res1.get_data(as_text=True)
        self.assertIn("RECU DE PAIEMENT", html)
        self.assertIn("Mamadou Diallo", html)
        self.assertIn("6e A", html)
        token1 = Paiement.query.get(self.paiement_a.id).verification_token
        self.assertTrue(token1)

        res2 = self.client.get(f"/paiements/{self.paiement_a.id}")
        self.assertEqual(res2.status_code, 200)
        token2 = Paiement.query.get(self.paiement_a.id).verification_token
        self.assertEqual(token1, token2)

    def test_private_receipt_other_school_refused(self):
        self._login(self.admin_b)
        res = self.client.get(f"/paiements/{self.paiement_a.id}")
        self.assertEqual(res.status_code, 404)

    def test_public_verification_minimal_and_no_private_leak(self):
        with self.app.test_request_context():
            build_payment_receipt_context(self.paiement_a)
            db.session.commit()
        token = Paiement.query.get(self.paiement_a.id).verification_token

        res = self.client.get(f"/verifier-recu/{token}")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("Recu authentique", html)
        self.assertIn("Ecole Alpha", html)
        self.assertIn("25 000 FCFA", html)
        self.assertNotIn("Mamadou", html)
        self.assertNotIn("Diallo", html)
        self.assertNotIn("92222222", html)
        self.assertNotIn("parent@example.test", html)
        self.assertNotIn("REF-SECRET-001", html)

    def test_public_invalid_token_is_neutral(self):
        res = self.client.get("/verifier-recu/not-a-valid-token")
        self.assertEqual(res.status_code, 404)
        html = res.get_data(as_text=True)
        self.assertIn("Recu introuvable", html)
        self.assertNotIn("Mamadou", html)
        self.assertNotIn("Diallo", html)

    def test_pdf_is_valid_and_uses_same_receipt_number(self):
        self._login(self.admin_a)
        self.client.get(f"/paiements/{self.paiement_a.id}")
        res = self.client.get(f"/paiements/{self.paiement_a.id}/pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF"))
        self.assertIn("recu-paiement-000001.pdf", res.headers.get("Content-Disposition", ""))

    def test_tokens_are_distinct_and_qr_has_only_verification_url(self):
        with self.app.test_request_context():
            ctx_a = build_payment_receipt_context(self.paiement_a)
            ctx_b = build_payment_receipt_context(self.paiement_b)
            db.session.commit()
        self.assertNotEqual(self.paiement_a.verification_token, self.paiement_b.verification_token)
        self.assertIn(f"/verifier-recu/{self.paiement_a.verification_token}", ctx_a["verification_url"])
        self.assertNotIn("Mamadou", ctx_a["verification_url"])
        self.assertNotIn("Diallo", ctx_a["verification_url"])
        self.assertNotIn("25000", ctx_a["verification_url"])
        self.assertIn("wa.me", ctx_a["whatsapp_url"])
        self.assertNotIn("Mamadou", ctx_a["whatsapp_url"])
        self.assertNotIn("Diallo", ctx_a["whatsapp_url"])


if __name__ == "__main__":
    unittest.main()
