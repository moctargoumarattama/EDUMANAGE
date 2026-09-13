import unittest
from datetime import date, time

from app import create_app, db
from app.config import Config
from app.models import (
    Absence,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    EmploiTemps,
    Inscription,
    NiveauScolaire,
    Note,
    Professeur,
    Utilisateur,
)
from app.services.classes_annuelles import preparer_structure_annee
from app.services.niveaux import creer_classe_depuis_niveau, ensure_ecole_niveau_configs, set_cycle_actif, set_niveau_actif


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2C2PreparationStructureTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()
        ensure_ecole_niveau_configs(self.ecole_a.id, commit=True)
        ensure_ecole_niveau_configs(self.ecole_b.id, commit=True)

        self.n6 = NiveauScolaire.query.filter_by(code="6E").first()
        self.n5 = NiveauScolaire.query.filter_by(code="5E").first()
        self.n4 = NiveauScolaire.query.filter_by(code="4E").first()
        self.n3 = NiveauScolaire.query.filter_by(code="3E").first()
        self.nci = NiveauScolaire.query.filter_by(code="CI").first()

        self.source = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.target = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        self.target_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole_b.id)
        db.session.add_all([self.source, self.target, self.target_b])
        db.session.commit()

        from app.services.structure_annuelle import sauvegarder_structure_annee
        college_nids = [self.n6.id, self.n5.id, self.n4.id, self.n3.id]
        sauvegarder_structure_annee(self.ecole_a.id, self.source.id, college_nids + [self.nci.id])
        sauvegarder_structure_annee(self.ecole_a.id, self.target.id, college_nids)
        sauvegarder_structure_annee(self.ecole_b.id, self.target_b.id, college_nids)
        db.session.commit()

        self.classes_source = {}
        for niveau, nom, section in [
            (self.n6, "6e A", "A"),
            (self.n6, "6e B", "B"),
            (self.n5, "5e A", "A"),
            (self.n4, "4e A", "A"),
            (self.n3, "3e A", "A"),
            (self.nci, "CI A", "A"),
        ]:
            classe, error = creer_classe_depuis_niveau(self.ecole_a.id, self.source.id, niveau.id, nom=nom, section=section, capacite=42)
            self.assertIsNone(error)
            self.classes_source[nom] = classe

        set_cycle_actif(self.ecole_a.id, "primaire", False)

        self.prof_ali_user = Utilisateur(nom="Ali", email="ali.prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.prof_mariama_user = Utilisateur(nom="Mariama", email="mariama.prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        db.session.add_all([self.prof_ali_user, self.prof_mariama_user])
        db.session.flush()

        self.prof_ali = Professeur(nom="Ali", prenom="Prof", specialite="Math", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_ali_user.id)
        self.prof_mariama = Professeur(nom="Mariama", prenom="Prof", specialite="Francais", ecole_id=self.ecole_a.id, utilisateur_id=self.prof_mariama_user.id)
        db.session.add_all([self.prof_ali, self.prof_mariama])
        db.session.flush()

        self.math_6a = Cours(nom="Mathematiques", description="Algebre", coefficient=4, ecole_id=self.ecole_a.id, classe_id=self.classes_source["6e A"].id, professeur_id=self.prof_ali.id)
        self.fr_6a = Cours(nom="Francais", description="Lecture", coefficient=3, ecole_id=self.ecole_a.id, classe_id=self.classes_source["6e A"].id, professeur_id=self.prof_mariama.id)
        self.math_6b = Cours(nom="Mathematiques", description="Algebre", coefficient=4, ecole_id=self.ecole_a.id, classe_id=self.classes_source["6e B"].id, professeur_id=self.prof_ali.id)
        self.fr_6b = Cours(nom="Francais", description="Lecture", coefficient=3, ecole_id=self.ecole_a.id, classe_id=self.classes_source["6e B"].id, professeur_id=self.prof_mariama.id)
        db.session.add_all([self.math_6a, self.fr_6a, self.math_6b, self.fr_6b])
        db.session.flush()

        self.eleve = Eleve(nom="Moussa", prenom="A", date_naissance=date(2014, 1, 1), ecole_id=self.ecole_a.id, classe_id=self.classes_source["6e A"].id)
        db.session.add(self.eleve)
        db.session.flush()
        db.session.add_all([
            Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.source.id, classe_id=self.classes_source["6e A"].id),
            Note(valeur=12, coefficient=1, eleve_id=self.eleve.id, cours_id=self.math_6a.id, ecole_id=self.ecole_a.id, annee_id=self.source.id),
            Absence(eleve_id=self.eleve.id, cours_id=self.math_6a.id, ecole_id=self.ecole_a.id, date_absence=date(2026, 1, 10)),
            EmploiTemps(professeur_id=self.prof_ali.id, jour="Lundi", heure_debut=time(8, 0), heure_fin=time(10, 0), cours_id=self.math_6a.id, classe_id=self.classes_source["6e A"].id, ecole_id=self.ecole_a.id),
        ])

        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.prof_user = Utilisateur(nom="ProfUser", email="prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        db.session.add_all([self.admin, self.prof_user, self.parent])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
        return client

    def test_preparation_copie_classes_et_cours_sans_donnees_historiques(self):
        result, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.source.id)
        self.assertIsNone(error)
        self.assertEqual(result["classes_creees"], 5)
        self.assertEqual(result["classes_ignorees_niveau_desactive"], 1)
        self.assertEqual(result["cours_crees"], 4)

        cible_6a = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="6e A").first()
        self.assertIsNotNone(cible_6a)
        self.assertNotEqual(cible_6a.id, self.classes_source["6e A"].id)
        self.assertEqual(cible_6a.niveau_id, self.classes_source["6e A"].niveau_id)
        self.assertEqual(cible_6a.section, "A")
        self.assertEqual(cible_6a.capacite, 42)
        self.assertEqual(cible_6a.statut, "ouverte")
        self.assertIsNone(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="CI A").first())

        copied_math = Cours.query.filter_by(ecole_id=self.ecole_a.id, classe_id=cible_6a.id, nom="Mathematiques").first()
        self.assertIsNotNone(copied_math)
        self.assertEqual(copied_math.description, "Algebre")
        self.assertEqual(copied_math.coefficient, 4)
        self.assertIsNone(copied_math.professeur_id)

        self.assertEqual(Inscription.query.filter_by(annee_scolaire_id=self.target.id).count(), 0)
        self.assertEqual(Note.query.filter_by(annee_id=self.target.id).count(), 0)
        self.assertEqual(Absence.query.join(Cours).filter(Cours.classe_id == cible_6a.id).count(), 0)
        self.assertEqual(EmploiTemps.query.filter_by(classe_id=cible_6a.id).count(), 0)
        self.assertEqual(Eleve.query.count(), 1)

    def test_idempotence_et_cible_existante_non_ecrasee(self):
        existing, error = creer_classe_depuis_niveau(self.ecole_a.id, self.target.id, self.n6.id, nom="6e A", section="A", capacite=12)
        self.assertIsNone(error)
        existing.statut = "fermee"
        manual_course = Cours(nom="Mathematiques", description="Manuel", coefficient=9, ecole_id=self.ecole_a.id, classe_id=existing.id, professeur_id=self.prof_ali.id)
        db.session.add(manual_course)
        db.session.commit()

        result, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.source.id)
        self.assertIsNone(error)
        self.assertEqual(result["classes_existantes"], 1)
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="6e A").count(), 1)
        db.session.refresh(existing)
        db.session.refresh(manual_course)
        self.assertEqual(existing.statut, "fermee")
        self.assertEqual(existing.capacite, 12)
        self.assertEqual(manual_course.description, "Manuel")
        self.assertEqual(manual_course.coefficient, 9)
        self.assertEqual(manual_course.professeur_id, self.prof_ali.id)

        second, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.source.id)
        self.assertIsNone(error)
        self.assertEqual(second["classes_creees"], 0)
        self.assertEqual(Cours.query.filter_by(ecole_id=self.ecole_a.id, classe_id=existing.id, nom="Mathematiques").count(), 1)

    def test_classe_source_fermee_cible_fermee_sans_cours(self):
        self.classes_source["6e B"].statut = "fermee"
        db.session.commit()
        result, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.source.id)
        self.assertIsNone(error)
        cible_6b = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.target.id, nom="6e B").first()
        self.assertEqual(cible_6b.statut, "fermee")
        self.assertEqual(Cours.query.filter_by(ecole_id=self.ecole_a.id, classe_id=cible_6b.id).count(), 0)
        self.assertEqual(result["cours_ignores_classe_fermee"], 2)

    def test_refus_source_cible_archivee_et_autre_ecole(self):
        result, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.target.id)
        self.assertIsNone(result)
        self.assertIn("differente", error)

        result, error = preparer_structure_annee(self.ecole_a.id, self.target_b.id, self.source.id)
        self.assertIsNone(result)
        self.assertIn("cible invalide", error.lower())

        result, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.target_b.id)
        self.assertIsNone(result)
        self.assertIn("source invalide", error.lower())

        self.target.statut = "archivee"
        db.session.commit()
        result, error = preparer_structure_annee(self.ecole_a.id, self.target.id, self.source.id)
        self.assertIsNone(result)
        self.assertIn("archivee", error)

    def test_route_admin_et_refus_roles_sans_changer_statut(self):
        client = self.login_as(self.admin)
        response = client.post(f"/annees/{self.target.id}/preparer-structure", data={"annee_source_id": self.source.id})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["result"]["classes_creees"], 5)
        self.assertEqual(data["result"]["cours_crees"], 4)
        self.assertEqual(db.session.get(AnneeScolaire, self.target.id).statut, "planifiee")

        with client.session_transaction() as session:
            self.assertNotIn("annee_consultee", session)

        prof_client = self.login_as(self.prof_user)
        self.assertEqual(prof_client.post(f"/annees/{self.target.id}/preparer-structure", json={}).status_code, 403)

        parent_client = self.login_as(self.parent)
        self.assertEqual(parent_client.post(f"/annees/{self.target.id}/preparer-structure", json={}).status_code, 403)

        self.target.statut = "archivee"
        db.session.commit()
        response = client.post(f"/annees/{self.target.id}/preparer-structure", data={"annee_source_id": self.source.id})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
