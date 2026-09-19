import unittest
from datetime import date
from unittest.mock import patch

from app import create_app, db
from app.access_codes import generate_access_code, is_valid_access_code
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Utilisateur


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class AccessCodes8DigitsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole A", onboarding_complete=True)
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
            nom="6e A",
            niveau="6e",
            section="A",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        self.admin = Utilisateur(
            nom="Admin",
            email="admin@example.com",
            role="admin",
            ecole_id=self.ecole.id,
        )
        self.admin.set_mot_de_passe("99999999")
        db.session.add_all([self.classe, self.admin])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
            session["annee_consultee"] = {str(self.ecole.id): self.annee.id}
        return client

    def professeur_data(self, email="prof@example.com", code_prof=""):
        return {
            "nom": "Diallo",
            "prenom": "Moussa",
            "date_naissance": "1985-01-01",
            "telephone": "90000000",
            "email": email,
            "adresse": "Niamey",
            "specialite": "Non renseignée",
            "matieres_enseignees": "Science",
            "code_prof": code_prof,
        }

    def eleve_data(self, email="parent@example.com", code_parent="", parent_id="0"):
        return {
            "nom": "Sow",
            "prenom": "Awa",
            "genre": "F",
            "date_naissance": "2014-01-01",
            "lieu_naissance": "Niamey",
            "classe_id": str(self.classe.id),
            "frais_annuels": "150000",
            "parent_id": parent_id,
            "parent_nom": "Parent Sow",
            "parent_email": email,
            "parent_telephone": "91000000",
            "code_parent": code_parent,
            "adresse": "Niamey",
        }

    def test_generate_access_code_format_and_leading_zero(self):
        with patch("app.access_codes.secrets.choice", side_effect=list("04182736")):
            code = generate_access_code()

        self.assertIsInstance(code, str)
        self.assertEqual(code, "04182736")
        self.assertTrue(is_valid_access_code(code))

    def test_professeur_auto_manual_and_invalid_codes(self):
        client = self.login_admin()
        with patch("app.routes.professeurs.generate_access_code", return_value="47295106"), \
             patch("app.notifications.envoyer_email", return_value=True):
            response = client.post("/ajouter_professeur", data=self.professeur_data(), follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        prof_user = Utilisateur.query.filter_by(email="prof@example.com", role="professeur").first()
        self.assertIsNotNone(prof_user)
        self.assertNotEqual(prof_user.mot_de_passe, "47295106")
        self.assertTrue(prof_user.check_mot_de_passe("47295106"))

        with patch("app.notifications.envoyer_email", return_value=True):
            response = client.post(
                "/ajouter_professeur",
                data=self.professeur_data(email="prof2@example.com", code_prof="12345678"),
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        prof_user = Utilisateur.query.filter_by(email="prof2@example.com", role="professeur").first()
        self.assertTrue(prof_user.check_mot_de_passe("12345678"))

        response = client.post(
            "/ajouter_professeur",
            data=self.professeur_data(email="bad1@example.com", code_prof="1234"),
            follow_redirects=True,
        )
        self.assertIn("Le code d'acc", response.get_data(as_text=True))
        self.assertIsNone(Utilisateur.query.filter_by(email="bad1@example.com").first())

        response = client.post(
            "/ajouter_professeur",
            data=self.professeur_data(email="bad2@example.com", code_prof="abcdefgh"),
            follow_redirects=True,
        )
        self.assertIn("Le code d'acc", response.get_data(as_text=True))
        self.assertIsNone(Utilisateur.query.filter_by(email="bad2@example.com").first())

    def test_parent_auto_manual_and_existing_parent_password_preserved(self):
        client = self.login_admin()
        with patch("app.routes.eleves.generate_access_code", return_value="58310427"), \
             patch("app.notifications.envoyer_email", return_value=True):
            response = client.post("/ajouter_eleve", data=self.eleve_data(), follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        parent = Utilisateur.query.filter_by(email="parent@example.com", role="parent").first()
        self.assertIsNotNone(parent)
        self.assertNotEqual(parent.mot_de_passe, "58310427")
        self.assertTrue(parent.check_mot_de_passe("58310427"))

        with patch("app.notifications.envoyer_email", return_value=True):
            response = client.post(
                "/ajouter_eleve",
                data=self.eleve_data(email="parent2@example.com", code_parent="87654321"),
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        parent2 = Utilisateur.query.filter_by(email="parent2@example.com", role="parent").first()
        self.assertTrue(parent2.check_mot_de_passe("87654321"))

        parent2_hash = parent2.mot_de_passe
        response = client.post(
            "/ajouter_eleve",
            data=self.eleve_data(email="", code_parent="", parent_id=str(parent2.id)),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(parent2)
        self.assertEqual(parent2.mot_de_passe, parent2_hash)

    def test_parent_invalid_code_refused(self):
        client = self.login_admin()
        response = client.post(
            "/ajouter_eleve",
            data=self.eleve_data(email="badparent@example.com", code_parent="1234"),
            follow_redirects=True,
        )
        self.assertIn("Le code d'acc", response.get_data(as_text=True))
        self.assertIsNone(Utilisateur.query.filter_by(email="badparent@example.com").first())

    def test_admin_reset_uses_canonical_generator(self):
        client = self.login_admin()
        user = Utilisateur(nom="User", email="user@example.com", role="parent", ecole_id=self.ecole.id)
        user.set_mot_de_passe("11112222")
        db.session.add(user)
        db.session.commit()

        with patch("app.routes.utilisateurs.generate_access_code", return_value="13572468"):
            response = client.post(f"/admin/utilisateur/{user.id}/reset-password")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["password"], "13572468")
        db.session.refresh(user)
        self.assertNotEqual(user.mot_de_passe, "13572468")
        self.assertTrue(user.check_mot_de_passe("13572468"))


if __name__ == "__main__":
    unittest.main()
