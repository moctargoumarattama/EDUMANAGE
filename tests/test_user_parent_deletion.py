import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Utilisateur


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class UserParentDeletionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole A", onboarding_complete=True)
        self.autre_ecole = Ecole(nom="Ecole B", onboarding_complete=True)
        db.session.add_all([self.ecole, self.autre_ecole])
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole.id,
        )
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
        db.session.add_all([self.annee, self.classe])
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="admin@example.com", role="admin", ecole_id=self.ecole.id)
        self.admin.set_mot_de_passe("secret")
        self.parent = Utilisateur(nom="Parent", email="parent@example.com", role="parent", ecole_id=self.ecole.id)
        self.parent.set_mot_de_passe("secret")
        self.parent_sans_enfant = Utilisateur(
            nom="Parent Libre",
            email="parent-libre@example.com",
            role="parent",
            ecole_id=self.ecole.id,
        )
        self.parent_sans_enfant.set_mot_de_passe("secret")
        self.parent_autre_ecole = Utilisateur(
            nom="Parent B",
            email="parent-b@example.com",
            role="parent",
            ecole_id=self.autre_ecole.id,
        )
        self.parent_autre_ecole.set_mot_de_passe("secret")
        db.session.add_all([self.admin, self.parent, self.parent_sans_enfant, self.parent_autre_ecole])
        db.session.flush()

        self.eleve = Eleve(
            nom="Diallo",
            prenom="Awa",
            date_naissance=date(2014, 1, 1),
            ecole_id=self.ecole.id,
            parent_id=self.parent.id,
        )
        db.session.add(self.eleve)
        db.session.flush()

        self.inscription = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            statut="inscrit",
        )
        db.session.add(self.inscription)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
            session["ecole_id"] = self.ecole.id
        return client

    def test_refuse_parent_with_children_and_preserve_data(self):
        client = self.client_as_admin()
        response = client.delete(f"/api/users/{self.parent.id}")

        self.assertEqual(response.status_code, 400)
        self.assertIn("rattach", response.get_json()["message"])
        self.assertIsNotNone(Utilisateur.query.get(self.parent.id))
        self.assertIsNotNone(Eleve.query.get(self.eleve.id))
        self.assertIsNotNone(Inscription.query.get(self.inscription.id))

    def test_allow_parent_without_children(self):
        client = self.client_as_admin()
        parent_id = self.parent_sans_enfant.id
        response = client.delete(f"/api/users/{parent_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(Utilisateur.query.get(parent_id))
        self.assertIsNotNone(Eleve.query.get(self.eleve.id))
        self.assertIsNotNone(Inscription.query.get(self.inscription.id))

    def test_refuse_self_delete(self):
        client = self.client_as_admin()
        response = client.delete(f"/api/users/{self.admin.id}")

        self.assertIn(response.status_code, (400, 403))
        self.assertIsNotNone(Utilisateur.query.get(self.admin.id))

    def test_refuse_cross_tenant_delete(self):
        client = self.client_as_admin()
        response = client.delete(f"/api/users/{self.parent_autre_ecole.id}")

        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(Utilisateur.query.get(self.parent_autre_ecole.id))

    def test_admin_utilisateur_route_refuses_parent_with_children(self):
        client = self.client_as_admin()
        response = client.delete(f"/admin/utilisateur/{self.parent.id}")

        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(Utilisateur.query.get(self.parent.id))
        self.assertIsNotNone(Eleve.query.get(self.eleve.id))
        self.assertIsNotNone(Inscription.query.get(self.inscription.id))


if __name__ == "__main__":
    unittest.main()
