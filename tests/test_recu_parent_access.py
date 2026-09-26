from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Paiement, Utilisateur
from app.services.payment_receipts import build_payment_receipt_context


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-recu-parent-access"
    SERVER_NAME = "klasora.test"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class RecuParentAccessTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole Klasora", adresse="Niamey", telephone="90000000", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()

        self.classe = Classe(nom="CI A", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="pass", role="admin", ecole_id=self.ecole.id)
        self.parent = Utilisateur(
            nom="Parent Diallo",
            email=None,
            telephone="+227 90 12 34 56",
            mot_de_passe="hash-non-utilise",
            role="parent",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.classe, self.admin, self.parent])
        db.session.flush()

        self.eleve = Eleve(
            nom="Diallo",
            prenom="Amina",
            date_naissance=date(2018, 3, 12),
            contact_parent="90-12-34-56",
            code_parent="123456",
            parent_id=self.parent.id,
            ecole_id=self.ecole.id,
        )
        db.session.add(self.eleve)
        db.session.flush()

        self.inscription = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_annuels=100000,
        )
        db.session.add(self.inscription)
        db.session.flush()

        self.paiement = Paiement(
            montant=15000,
            mois="Inscription",
            annee=2026,
            mode_paiement="Espèces",
            statut="payé",
            reference="INS-001",
            eleve_id=self.eleve.id,
            inscription_id=self.inscription.id,
            ecole_id=self.ecole.id,
            date_paiement=datetime(2026, 9, 5, 8, 45),
        )
        db.session.add(self.paiement)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["ecole_id"] = self.ecole.id
            sess["annee_consultee_id"] = self.annee.id

    def test_receipt_context_exposes_parent_access_coupon_values(self):
        with self.app.test_request_context():
            context = build_payment_receipt_context(self.paiement)

        self.assertEqual(context["parent_telephone"], "90123456")
        self.assertEqual(context["parent_pin"], "123456")
        self.assertEqual(context["parent_access_site"], "https://klasora.com")

    def test_receipt_prints_detachable_parent_access_coupon(self):
        self._login_admin()

        response = self.client.get(f"/paiements/{self.paiement.id}/recu")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("coupon-parent-decoupe", html)
        self.assertIn("ESPACE PARENT KLASORA - VOS ACCES DE CONNEXION", html)
        self.assertIn("https://klasora.com", html)
        self.assertIn("90123456", html)
        self.assertIn("123456", html)
        self.assertIn("Amina Diallo", html)


if __name__ == "__main__":
    unittest.main()
