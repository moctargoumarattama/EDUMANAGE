import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Cours, Ecole, Eleve, Note, Professeur, Utilisateur


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class AjaxDeletionsTestCase(unittest.TestCase):
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
        self.autre_annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.autre_ecole.id,
        )
        db.session.add_all([self.annee, self.autre_annee])
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="admin@example.com", role="admin", ecole_id=self.ecole.id)
        self.admin.set_mot_de_passe("secret")
        self.autre_admin = Utilisateur(nom="Admin B", email="adminb@example.com", role="admin", ecole_id=self.autre_ecole.id)
        self.autre_admin.set_mot_de_passe("secret")
        db.session.add_all([self.admin, self.autre_admin])
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

    def ajax_headers(self):
        return {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

    def make_classe(self, nom="6e A", ecole=None, annee=None):
        classe = Classe(
            nom=nom,
            niveau="6e",
            section="A",
            capacite=35,
            capacite_max=35,
            statut="ouverte",
            ecole_id=(ecole or self.ecole).id,
            annee_scolaire_id=(annee or self.annee).id,
        )
        db.session.add(classe)
        db.session.flush()
        return classe

    def make_eleve(self, nom="Diallo", ecole=None):
        eleve = Eleve(
            nom=nom,
            prenom="Awa",
            date_naissance=date(2014, 1, 1),
            ecole_id=(ecole or self.ecole).id,
        )
        db.session.add(eleve)
        db.session.flush()
        return eleve

    def make_professeur(self, nom="Moussa", ecole=None):
        ecole = ecole or self.ecole
        user = Utilisateur(
            nom=f"User {nom}",
            email=f"{nom.lower()}-{ecole.id}@example.com",
            role="professeur",
            ecole_id=ecole.id,
        )
        user.set_mot_de_passe("secret")
        db.session.add(user)
        db.session.flush()
        prof = Professeur(
            nom=nom,
            prenom="Prof",
            email=user.email,
            specialite="Science",
            matieres_enseignees="Science",
            code_prof=f"CODE{user.id}",
            ecole_id=ecole.id,
            utilisateur_id=user.id,
        )
        db.session.add(prof)
        db.session.flush()
        return prof

    def make_cours(self, nom="Science", ecole=None, classe=None, professeur=None):
        ecole = ecole or self.ecole
        cours = Cours(
            nom=nom,
            coefficient=1,
            ecole_id=ecole.id,
            classe_id=classe.id if classe else None,
            professeur_id=professeur.id if professeur else None,
        )
        db.session.add(cours)
        db.session.flush()
        return cours

    def assert_ajax_delete_success(self, endpoint, obj_id, model):
        client = self.login_admin()
        response = client.post(endpoint, headers=self.ajax_headers())
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deleted_id"], obj_id)
        self.assertIsNone(db.session.get(model, obj_id))

    def test_eleve_delete_html_ajax_missing_other_school_and_constraint(self):
        client = self.login_admin()
        eleve = self.make_eleve("Html")
        db.session.commit()
        response = client.post(f"/eleve/{eleve.id}/supprimer")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(Eleve, eleve.id))

        eleve = self.make_eleve("Ajax")
        db.session.commit()
        self.assert_ajax_delete_success(f"/eleve/{eleve.id}/supprimer", eleve.id, Eleve)

        response = client.post("/eleve/999999/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["success"])

        autre = self.make_eleve("Other", self.autre_ecole)
        db.session.commit()
        response = client.post(f"/eleve/{autre.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(db.session.get(Eleve, autre.id))

        classe = self.make_classe("6e B")
        cours = self.make_cours(classe=classe)
        bloque = self.make_eleve("Blocked")
        db.session.add(Note(valeur=12, coefficient=1, eleve_id=bloque.id, cours_id=cours.id, ecole_id=self.ecole.id, annee_id=self.annee.id))
        db.session.commit()
        response = client.post(f"/eleve/{bloque.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.session.get(Eleve, bloque.id))

    def test_professeur_delete_html_ajax_missing_other_school_and_constraint(self):
        client = self.login_admin()
        prof = self.make_professeur("HtmlProf")
        db.session.commit()
        response = client.post(f"/professeur/{prof.id}/supprimer")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(Professeur, prof.id))

        prof = self.make_professeur("AjaxProf")
        db.session.commit()
        self.assert_ajax_delete_success(f"/professeur/{prof.id}/supprimer", prof.id, Professeur)

        response = client.post("/professeur/999999/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)

        autre = self.make_professeur("OtherProf", self.autre_ecole)
        db.session.commit()
        response = client.post(f"/professeur/{autre.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(db.session.get(Professeur, autre.id))

        bloque = self.make_professeur("BusyProf")
        self.make_cours(professeur=bloque)
        db.session.commit()
        response = client.post(f"/professeur/{bloque.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.session.get(Professeur, bloque.id))

    def test_classe_delete_html_ajax_missing_other_school_and_constraint(self):
        client = self.login_admin()
        classe = self.make_classe("Html Class")
        db.session.commit()
        response = client.post(f"/classes/{classe.id}/supprimer")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(Classe, classe.id))

        classe = self.make_classe("Ajax Class")
        db.session.commit()
        self.assert_ajax_delete_success(f"/classes/{classe.id}/supprimer", classe.id, Classe)

        response = client.post("/classes/999999/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)

        autre = self.make_classe("Other Class", self.autre_ecole, self.autre_annee)
        db.session.commit()
        response = client.post(f"/classes/{autre.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(db.session.get(Classe, autre.id))

        bloque = self.make_classe("Busy Class")
        self.make_cours(classe=bloque)
        db.session.commit()
        response = client.post(f"/classes/{bloque.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.session.get(Classe, bloque.id))

    def test_cours_delete_html_ajax_missing_other_school_and_constraint(self):
        client = self.login_admin()
        cours = self.make_cours("Html Course")
        db.session.commit()
        response = client.post(f"/cours/{cours.id}/supprimer")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(Cours, cours.id))

        cours = self.make_cours("Ajax Course")
        db.session.commit()
        self.assert_ajax_delete_success(f"/cours/{cours.id}/supprimer", cours.id, Cours)

        response = client.post("/cours/999999/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)

        autre = self.make_cours("Other Course", self.autre_ecole)
        db.session.commit()
        response = client.post(f"/cours/{autre.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 404)
        self.assertIsNotNone(db.session.get(Cours, autre.id))

        classe = self.make_classe("Notes Class")
        bloque = self.make_cours("Blocked Course", classe=classe)
        eleve = self.make_eleve("CoursBlocked")
        db.session.add(Note(valeur=12, coefficient=1, eleve_id=eleve.id, cours_id=bloque.id, ecole_id=self.ecole.id, annee_id=self.annee.id))
        db.session.commit()
        response = client.post(f"/cours/{bloque.id}/supprimer", headers=self.ajax_headers())
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.session.get(Cours, bloque.id))


if __name__ == "__main__":
    unittest.main()
