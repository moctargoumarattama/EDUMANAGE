import unittest
from datetime import date
from unittest.mock import patch

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Utilisateur


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class EleveParentPhoneOnlyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole Niger", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.flush()
        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()
        self.classe = Classe(
            nom="CI A",
            niveau="CI",
            statut="ouverte",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        self.admin = Utilisateur(
            nom="Admin",
            email="admin-phone-only@test.local",
            role="admin",
            ecole_id=self.ecole.id,
        )
        self.admin.set_mot_de_passe("secret")
        db.session.add_all([self.classe, self.admin])
        db.session.commit()

        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True
            sess["annee_consultee"] = {str(self.ecole.id): self.annee.id}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def eleve_payload(self, telephone="+227 90 12 34 56", email="parent.interdit@test.local"):
        return {
            "nom": "Moussa",
            "prenom": "Amina",
            "genre": "F",
            "date_naissance": "2016-01-01",
            "lieu_naissance": "Niamey",
            "adresse": "Niamey",
            "classe_id": str(self.classe.id),
            "frais_annuels": "150000",
            "parent_id": "0",
            "parent_nom": "Tuteur Amina",
            "parent_telephone": telephone,
            "parent_email": email,
            "code_parent": "",
        }

    def test_create_student_with_phone_only_parent(self):
        payload = self.eleve_payload(email="")

        with patch("app.routes.eleves.generate_access_code", return_value="12345678"):
            response = self.client.post("/ajouter_eleve", data=payload, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        parent = Utilisateur.query.filter_by(role="parent", telephone="90123456").first()
        self.assertIsNotNone(parent)
        self.assertIsNone(parent.email)
        self.assertTrue(parent.check_mot_de_passe("12345678"))

        eleve = Eleve.query.filter_by(nom="Moussa", prenom="Amina").first()
        self.assertIsNotNone(eleve)
        self.assertEqual(eleve.contact_parent, "90123456")
        self.assertIsNone(eleve.email_parent)
        self.assertEqual(eleve.parent_id, parent.id)

    def test_submitted_parent_email_is_ignored(self):
        with patch("app.routes.eleves.generate_access_code", return_value="87654321"):
            response = self.client.post("/ajouter_eleve", data=self.eleve_payload(), follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        parent = Utilisateur.query.filter_by(role="parent", telephone="90123456").first()
        self.assertIsNotNone(parent)
        self.assertIsNone(parent.email)
        self.assertIsNone(Eleve.query.filter_by(contact_parent="90123456").first().email_parent)


if __name__ == "__main__":
    unittest.main()
