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
from app.services.classes_annuelles import get_classes_ouvertes_annee, set_classe_ouverte
from app.services.cours_annuels import valider_classe_pour_nouveau_cours
from app.services.inscriptions_annuelles import creer_inscription_annuelle, modifier_inscription_annuelle
from app.services.niveaux import creer_classe_depuis_niveau, ensure_ecole_niveau_configs, set_niveau_actif


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2C3ClassesOuvertesTestCase(unittest.TestCase):
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
        self.active = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.planifiee = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole_a.id)
        self.archivee = AnneeScolaire(nom="2024-2025", date_debut=date(2024, 9, 1), date_fin=date(2025, 7, 31), statut="active", ecole_id=self.ecole_a.id)
        self.annee_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31), statut="planifiee", ecole_id=self.ecole_b.id)
        db.session.add_all([self.active, self.planifiee, self.archivee, self.annee_b])
        db.session.commit()

        self.classe_active, error = creer_classe_depuis_niveau(self.ecole_a.id, self.active.id, self.n6.id, nom="6e A", section="A")
        self.assertIsNone(error)
        self.classe_planifiee, error = creer_classe_depuis_niveau(self.ecole_a.id, self.planifiee.id, self.n6.id, nom="6e A", section="A")
        self.assertIsNone(error)
        self.classe_planifiee_b, error = creer_classe_depuis_niveau(self.ecole_a.id, self.planifiee.id, self.n5.id, nom="5e A", section="A")
        self.assertIsNone(error)
        self.classe_archivee, error = creer_classe_depuis_niveau(self.ecole_a.id, self.archivee.id, self.n6.id, nom="6e Archive", section="A")
        self.assertIsNone(error)
        self.archivee.statut = "archivee"
        self.classe_ecole_b, error = creer_classe_depuis_niveau(self.ecole_b.id, self.annee_b.id, self.n6.id, nom="6e B", section="B")
        self.assertIsNone(error)

        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.super_admin = Utilisateur(nom="Root", email="root@test.local", mot_de_passe="x", role="super_admin", ecole_id=None)
        self.prof_user = Utilisateur(nom="ProfUser", email="prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        self.professeur_user = Utilisateur(nom="Ali", email="ali@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        db.session.add_all([self.admin, self.super_admin, self.prof_user, self.parent, self.professeur_user])
        db.session.flush()
        self.professeur = Professeur(nom="Ali", prenom="Prof", specialite="Math", ecole_id=self.ecole_a.id, utilisateur_id=self.professeur_user.id)
        db.session.add(self.professeur)
        db.session.flush()

        self.eleve = Eleve(nom="Moussa", prenom="A", date_naissance=date(2014, 1, 1), ecole_id=self.ecole_a.id, classe_id=self.classe_active.id)
        db.session.add(self.eleve)
        db.session.flush()
        self.cours = Cours(nom="Mathematiques", description="Base", coefficient=4, ecole_id=self.ecole_a.id, classe_id=self.classe_active.id, professeur_id=self.professeur.id)
        db.session.add(self.cours)
        db.session.flush()
        self.inscription = Inscription(ecole_id=self.ecole_a.id, eleve_id=self.eleve.id, annee_scolaire_id=self.active.id, classe_id=self.classe_active.id)
        self.note = Note(valeur=14, coefficient=1, eleve_id=self.eleve.id, cours_id=self.cours.id, ecole_id=self.ecole_a.id, annee_id=self.active.id)
        self.absence = Absence(eleve_id=self.eleve.id, cours_id=self.cours.id, ecole_id=self.ecole_a.id, date_absence=date(2026, 1, 10))
        self.emploi = EmploiTemps(professeur_id=self.professeur.id, jour="Lundi", heure_debut=time(8, 0), heure_fin=time(10, 0), cours_id=self.cours.id, classe_id=self.classe_active.id, ecole_id=self.ecole_a.id)
        db.session.add_all([self.inscription, self.note, self.absence, self.emploi])
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
            if ecole_id is not None:
                session["ecole_id"] = ecole_id
        return client

    def test_statut_service_idempotent_active_planifiee_archivee_et_multi_ecoles(self):
        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_planifiee.id, False)
        self.assertIsNone(error)
        self.assertEqual(classe.statut, "fermee")

        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_planifiee.id, False)
        self.assertIsNone(error)
        self.assertEqual(classe.statut, "fermee")

        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_planifiee.id, True)
        self.assertIsNone(error)
        self.assertEqual(classe.statut, "ouverte")

        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_active.id, False)
        self.assertIsNone(error)
        self.assertEqual(classe.statut, "fermee")

        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_archivee.id, False)
        self.assertIsNone(classe)
        self.assertIn("archivee", error)

        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_ecole_b.id, False)
        self.assertIsNone(classe)
        self.assertIn("introuvable", error)

    def test_route_statut_admin_super_admin_roles_et_contexte_ecole(self):
        client = self.login_as(self.admin)
        response = client.post(f"/classes/{self.classe_planifiee.id}/statut", json={"statut": "fermee"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["statut"], "fermee")

        response = client.post(f"/classes/{self.classe_planifiee.id}/statut", json={"statut": "ouverte"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["statut"], "ouverte")

        response = client.post(f"/classes/{self.classe_ecole_b.id}/statut", json={"statut": "fermee"})
        self.assertEqual(response.status_code, 400)

        super_client = self.login_as(self.super_admin, self.ecole_a.id)
        response = super_client.post(f"/classes/{self.classe_planifiee.id}/statut", json={"statut": "fermee"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["statut"], "fermee")

        response = super_client.post(f"/classes/{self.classe_ecole_b.id}/statut", json={"statut": "fermee"})
        self.assertEqual(response.status_code, 400)

        super_client_b = self.login_as(self.super_admin, self.ecole_b.id)
        response = super_client_b.post(f"/classes/{self.classe_ecole_b.id}/statut", json={"statut": "fermee"})
        self.assertEqual(response.status_code, 200)

        prof_client = self.login_as(self.prof_user)
        self.assertEqual(prof_client.post(f"/classes/{self.classe_planifiee.id}/statut", json={"statut": "fermee"}).status_code, 403)
        parent_client = self.login_as(self.parent)
        self.assertEqual(parent_client.post(f"/classes/{self.classe_planifiee.id}/statut", json={"statut": "fermee"}).status_code, 403)

        response = client.post(f"/classes/{self.classe_archivee.id}/statut", json={"statut": "fermee"})
        self.assertEqual(response.status_code, 400)

    def test_inscriptions_existantes_conservees_et_nouvelles_inscriptions_filtrees(self):
        eleve_classe_id = self.eleve.classe_id
        inscriptions_avant = Inscription.query.count()

        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_active.id, False)
        self.assertIsNone(error)
        self.assertEqual(Inscription.query.count(), inscriptions_avant)
        self.assertEqual(db.session.get(Eleve, self.eleve.id).classe_id, eleve_classe_id)
        self.assertEqual(db.session.get(Inscription, self.inscription.id).classe_id, self.classe_active.id)

        nouvel_eleve = Eleve(nom="Awa", prenom="B", date_naissance=date(2014, 2, 1), ecole_id=self.ecole_a.id, classe_id=self.classe_planifiee.id)
        db.session.add(nouvel_eleve)
        db.session.flush()
        inscription, error = creer_inscription_annuelle(self.ecole_a.id, nouvel_eleve.id, self.active.id, self.classe_active.id)
        self.assertIsNone(inscription)
        self.assertIn("fermee", error)

        inscription, error = creer_inscription_annuelle(self.ecole_a.id, nouvel_eleve.id, self.planifiee.id, self.classe_planifiee.id)
        self.assertIsNone(error)
        self.assertEqual(inscription.classe_id, self.classe_planifiee.id)

        target_closed, error = set_classe_ouverte(self.ecole_a.id, self.classe_planifiee_b.id, False)
        self.assertIsNone(error)
        inscription, error = modifier_inscription_annuelle(self.ecole_a.id, nouvel_eleve.id, self.planifiee.id, target_closed.id)
        self.assertIsNone(inscription)
        self.assertIn("fermee", error)

    def test_creation_cours_classe_ouverte_fermee_archivee_et_conservation_existants(self):
        classe, error = valider_classe_pour_nouveau_cours(self.ecole_a.id, self.classe_planifiee.id)
        self.assertIsNone(error)
        self.assertEqual(classe.id, self.classe_planifiee.id)

        set_classe_ouverte(self.ecole_a.id, self.classe_planifiee.id, False)
        classe, error = valider_classe_pour_nouveau_cours(self.ecole_a.id, self.classe_planifiee.id)
        self.assertIsNone(classe)
        self.assertIn("fermee", error)

        classe, error = valider_classe_pour_nouveau_cours(self.ecole_a.id, self.classe_archivee.id)
        self.assertIsNone(classe)
        self.assertIn("archivee", error)

        notes_avant = Note.query.count()
        absences_avant = Absence.query.count()
        emplois_avant = EmploiTemps.query.count()
        professeur_id = self.cours.professeur_id

        set_classe_ouverte(self.ecole_a.id, self.classe_active.id, False)
        cours = db.session.get(Cours, self.cours.id)
        self.assertEqual(cours.professeur_id, professeur_id)
        self.assertEqual(Note.query.count(), notes_avant)
        self.assertEqual(Absence.query.count(), absences_avant)
        self.assertEqual(EmploiTemps.query.count(), emplois_avant)

        client = self.login_as(self.admin)
        count_before = Cours.query.filter_by(classe_id=self.classe_active.id).count()
        response = client.post("/ajouter_cours", data={
            "nom": "Physique",
            "description": "Intro",
            "coefficient": "2",
            "professeur_id": str(self.professeur.id),
            "classe_id": str(self.classe_active.id),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cours.query.filter_by(classe_id=self.classe_active.id).count(), count_before)

        set_classe_ouverte(self.ecole_a.id, self.classe_planifiee_b.id, True)
        response = client.post("/ajouter_cours", data={
            "nom": "Physique",
            "description": "Intro",
            "coefficient": "2",
            "professeur_id": str(self.professeur.id),
            "classe_id": str(self.classe_planifiee_b.id),
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Cours.query.filter_by(classe_id=self.classe_planifiee_b.id, nom="Physique").first())

    def test_filtre_classes_ouvertes_ecole_annee_et_niveau_desactive(self):
        set_classe_ouverte(self.ecole_a.id, self.classe_planifiee_b.id, False)
        noms = [
            c.nom for c in get_classes_ouvertes_annee(self.ecole_a.id, self.planifiee.id)
            .order_by(Classe.nom).all()
        ]
        self.assertEqual(noms, ["6e A"])
        self.assertNotIn(self.classe_ecole_b.nom, noms)

        noms_active = [
            c.nom for c in get_classes_ouvertes_annee(self.ecole_a.id, self.active.id)
            .order_by(Classe.nom).all()
        ]
        self.assertEqual(noms_active, ["6e A"])

        set_niveau_actif(self.ecole_a.id, self.n5.id, False)
        noms = [
            c.nom for c in get_classes_ouvertes_annee(self.ecole_a.id, self.planifiee.id)
            .order_by(Classe.nom).all()
        ]
        self.assertEqual(noms, ["6e A"])

    def test_fermeture_cible_ne_modifie_pas_classe_source(self):
        self.assertEqual(self.classe_active.statut, "ouverte")
        set_classe_ouverte(self.ecole_a.id, self.classe_planifiee.id, False)
        self.assertEqual(db.session.get(Classe, self.classe_planifiee.id).statut, "fermee")
        self.assertEqual(db.session.get(Classe, self.classe_active.id).statut, "ouverte")


if __name__ == "__main__":
    unittest.main()
