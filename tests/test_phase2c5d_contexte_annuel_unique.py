import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Cours, Ecole, Eleve, Inscription, Utilisateur


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2C5DContexteAnnuelUniqueTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.archivee = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="archivee", ecole_id=self.ecole_a.id)
        self.active = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.planifiee = AnneeScolaire(nom="2027-2028", date_debut=date(2027, 9, 1), date_fin=date(2028, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        self.annee_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="active", ecole_id=self.ecole_b.id)
        db.session.add_all([self.archivee, self.active, self.planifiee, self.annee_b])
        db.session.flush()

        self.classe_archivee = Classe(nom="Archive A", niveau="6e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.archivee.id)
        self.classe_active = Classe(nom="Active A", niveau="5e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id)
        self.classe_planifiee = Classe(nom="Planifiee A", niveau="4e", statut="ouverte", ecole_id=self.ecole_a.id, annee_scolaire_id=self.planifiee.id)
        self.classe_b = Classe(nom="Classe B", niveau="5e", statut="ouverte", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_archivee, self.classe_active, self.classe_planifiee, self.classe_b])
        db.session.flush()

        self.cours_active = Cours(nom="Cours actif", coefficient=1, ecole_id=self.ecole_a.id, classe_id=self.classe_active.id)
        self.cours_planifie = Cours(nom="Cours planifie", coefficient=1, ecole_id=self.ecole_a.id, classe_id=self.classe_planifiee.id)
        db.session.add_all([self.cours_active, self.cours_planifie])

        self.eleve = Eleve(nom="Eleve", prenom="A", date_naissance=date(2014, 1, 1), ecole_id=self.ecole_a.id, classe_id=self.classe_planifiee.id)
        db.session.add(self.eleve)
        db.session.flush()
        db.session.add_all([
            Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.active.id, classe_id=self.classe_active.id),
            Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.planifiee.id, classe_id=self.classe_planifiee.id),
        ])

        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.super_admin = Utilisateur(nom="Root", email="root@test.local", mot_de_passe="x", role="super_admin")
        self.prof = Utilisateur(nom="Prof", email="prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        db.session.add_all([self.admin, self.super_admin, self.prof, self.parent])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user, ecole_id=None):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            if ecole_id:
                session["ecole_id"] = ecole_id
        return client

    def session_year(self, client):
        with client.session_transaction() as session:
            return (session.get("annee_consultee") or {}).get(str(self.ecole_a.id))

    def test_seul_post_annees_consulter_change_le_contexte(self):
        client = self.login_as(self.admin)

        self.assertEqual(client.get("/classes").status_code, 200)
        self.assertIsNone(self.session_year(client))

        response = client.post(f"/annees/{self.planifiee.id}/consulter", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.session_year(client), self.planifiee.id)

        for url in ("/classes", "/eleves", "/cours"):
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Planifiee A", response.data)

        for url in ("/classes", "/eleves", "/cours"):
            response = client.get(f"{url}?annee_id={self.archivee.id}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.session_year(client), self.planifiee.id)

        self.assertEqual(client.get(f"/annees/{self.active.id}/structure").status_code, 200)
        self.assertEqual(self.session_year(client), self.planifiee.id)
        self.assertEqual(db.session.get(AnneeScolaire, self.archivee.id).statut, "archivee")

    def test_annee_autre_ecole_refusee_et_roles_limites(self):
        client = self.login_as(self.admin)
        self.assertEqual(client.post(f"/annees/{self.annee_b.id}/consulter").status_code, 302)
        self.assertIsNone(self.session_year(client))

        super_client = self.login_as(self.super_admin, ecole_id=self.ecole_a.id)
        self.assertEqual(super_client.post(f"/annees/{self.planifiee.id}/consulter").status_code, 302)
        self.assertEqual(self.session_year(super_client), self.planifiee.id)

        self.assertEqual(self.login_as(self.prof).post(f"/annees/{self.planifiee.id}/consulter", json={}).status_code, 403)
        self.assertEqual(self.login_as(self.parent).post(f"/annees/{self.planifiee.id}/consulter", json={}).status_code, 403)

    def test_pages_ne_contiennent_plus_de_selecteur_annee_local(self):
        client = self.login_as(self.admin)
        client.post(f"/annees/{self.planifiee.id}/consulter")
        for url in ("/classes", "/eleves", "/cours"):
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'name="annee_id"', response.data)
            self.assertIn("Année consultée".encode("utf-8"), response.data)
        self.assertIn(b"/consulter", client.get("/annees").data)


if __name__ == "__main__":
    unittest.main()
