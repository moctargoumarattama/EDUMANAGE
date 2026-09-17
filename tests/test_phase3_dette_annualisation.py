import unittest
from datetime import date
from flask import g
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Ecole, AnneeScolaire, Utilisateur, Classe, Eleve, Inscription, Cours, Professeur, NiveauScolaire, AnneeNiveauConfig, EcoleNiveauConfig
from app.authorization import can_access_eleve

class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

class Phase3DetteAnnualisationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Écoles (Établissement A et Établissement B)
        self.ecole_a = Ecole(nom="École A")
        self.ecole_b = Ecole(nom="École B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # 2. Années scolaires pour École A
        self.annee_2025 = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id
        )
        self.annee_2026 = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id
        )
        # Année pour École B
        self.annee_b_2025 = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.annee_2025, self.annee_2026, self.annee_b_2025])
        db.session.flush()

        self.niv_6e = NiveauScolaire(code="6E", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niv_6e)
        db.session.flush()

        self.enc_6e_a = EcoleNiveauConfig(ecole_id=self.ecole_a.id, niveau_id=self.niv_6e.id, actif=True)
        self.enc_6e_b = EcoleNiveauConfig(ecole_id=self.ecole_b.id, niveau_id=self.niv_6e.id, actif=True)
        self.cfg_6e = AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2025.id, niveau_id=self.niv_6e.id, actif=True)
        self.cfg_6e_b = AnneeNiveauConfig(ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b_2025.id, niveau_id=self.niv_6e.id, actif=True)
        db.session.add_all([self.enc_6e_a, self.enc_6e_b, self.cfg_6e, self.cfg_6e_b])
        db.session.flush()

        # 3. Classes École A
        self.classe_6a = Classe(nom="6e A", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2025.id, niveau_id=self.niv_6e.id)
        self.classe_5b = Classe(nom="5e B", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2026.id, niveau_id=self.niv_6e.id)
        # Classe École B
        self.classe_b_6a = Classe(nom="6e A (École B)", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b_2025.id, niveau_id=self.niv_6e.id)
        db.session.add_all([self.classe_6a, self.classe_5b, self.classe_b_6a])
        db.session.flush()

        # 4. Professeurs
        # Prof 1 enseigne en 6e A (2025-2026)
        self.user_prof1 = Utilisateur(
            nom="Prof", prenom="Un", email="prof1@ecole-a.com",
            role="professeur", ecole_id=self.ecole_a.id, mot_de_passe=generate_password_hash("password")
        )
        self.user_prof2 = Utilisateur(
            nom="Prof", prenom="Deux", email="prof2@ecole-a.com",
            role="professeur", ecole_id=self.ecole_a.id, mot_de_passe=generate_password_hash("password")
        )
        self.admin_a = Utilisateur(
            nom="Admin", prenom="A", email="admin@ecole-a.com",
            role="admin", ecole_id=self.ecole_a.id, mot_de_passe=generate_password_hash("password")
        )
        db.session.add_all([self.user_prof1, self.user_prof2, self.admin_a])
        db.session.flush()

        self.prof1 = Professeur(id=self.user_prof1.id, utilisateur_id=self.user_prof1.id, nom="Prof", prenom="Un", ecole_id=self.ecole_a.id)
        self.prof2 = Professeur(id=self.user_prof2.id, utilisateur_id=self.user_prof2.id, nom="Prof", prenom="Deux", ecole_id=self.ecole_a.id)
        db.session.add_all([self.prof1, self.prof2])
        db.session.flush()

        # Cours 6e A pour Prof 1
        self.cours_math = Cours(nom="Maths", ecole_id=self.ecole_a.id, classe_id=self.classe_6a.id, professeur_id=self.prof1.id)
        # Cours 5e B pour Prof 2
        self.cours_fr = Cours(nom="Français", ecole_id=self.ecole_a.id, classe_id=self.classe_5b.id, professeur_id=self.prof2.id)
        db.session.add_all([self.cours_math, self.cours_fr])
        db.session.flush()

        # 5. Élève A (Élève permanent)
        self.eleve_a = Eleve(nom="Diop", prenom="Amadou", date_naissance=date(2010, 1, 1), ecole_id=self.ecole_a.id, classe_id=None) # Volontairement None !
        self.eleve_b_ecole = Eleve(nom="Sow", prenom="Fatou", date_naissance=date(2010, 5, 5), ecole_id=self.ecole_b.id, classe_id=self.classe_b_6a.id)
        db.session.add_all([self.eleve_a, self.eleve_b_ecole])
        db.session.flush()

        # 6. Inscriptions partielles / chronologiques
        # 2025-2026 : Élève A inscrit en 6e A
        self.insc_2025 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_a.id,
            classe_id=self.classe_6a.id,
            annee_scolaire_id=self.annee_2025.id,
            statut="inscrit"
        )
        # 2026-2027 : Élève A inscrit en 5e B
        self.insc_2026 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_a.id,
            classe_id=self.classe_5b.id,
            annee_scolaire_id=self.annee_2026.id,
            statut="inscrit"
        )
        db.session.add_all([self.insc_2025, self.insc_2026])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_1_professeur_annee_2025_voit_eleve_uniquement_si_affecte_6a(self):
        """1. Professeur 1 (6e A) a accès à l'élève en 2025-2026 alors que Eleve.classe_id est None."""
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.user_prof1)
            # Eleve.classe_id est None
            self.assertIsNone(self.eleve_a.classe_id)
            # can_access_eleve pour 2025-2026
            self.assertTrue(can_access_eleve(self.eleve_a, annee_scolaire_id=self.annee_2025.id))

    def test_2_professeur_5b_pas_de_droits_sur_annee_2025(self):
        """2. Professeur 2 (5e B) N'A PAS accès à l'élève pour l'année 2025-2026."""
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.user_prof2)
            self.assertFalse(can_access_eleve(self.eleve_a, annee_scolaire_id=self.annee_2025.id))

    def test_3_changement_eleve_classe_id_ne_change_aucune_permission_annuelle(self):
        """3. Modifier ou altérer Eleve.classe_id ne change pas les permissions basées sur Inscription."""
        # Altérer faussement Eleve.classe_id vers la classe 5e B
        self.eleve_a.classe_id = self.classe_5b.id
        db.session.commit()

        with self.app.test_request_context():
            from flask_login import login_user
            # Prof 1 (6e A) doit TOUJOURS avoir accès en 2025-2026 via Inscription
            login_user(self.user_prof1)
            self.assertTrue(can_access_eleve(self.eleve_a, annee_scolaire_id=self.annee_2025.id))

            # Prof 2 (5e B) ne doit TOUJOURS PAS avoir accès en 2025-2026
            login_user(self.user_prof2)
            self.assertFalse(can_access_eleve(self.eleve_a, annee_scolaire_id=self.annee_2025.id))

    def test_4_classe_avec_inscriptions_ne_peut_pas_etre_supprimee(self):
        """4. Une classe ayant des inscriptions ne peut pas être supprimée."""
        self.client.post("/login", data={"email": "admin@ecole-a.com", "mot_de_passe": "password"})
        res = self.client.post(f"/classes/{self.classe_6a.id}/supprimer", follow_redirects=True)
        self.assertIn("Impossible de supprimer une classe contenant des élèves", res.get_data(as_text=True))
        # Vérifier que la classe existe toujours
        self.assertIsNotNone(db.session.get(Classe, self.classe_6a.id))

    def test_5_edit_eleve_selectionne_classe_inscription(self):
        """5. edit_eleve sélectionne la classe basée sur Inscription active même si Eleve.classe_id est incorrect."""
        self.eleve_a.classe_id = None
        db.session.commit()

        self.client.post("/login", data={"email": "admin@ecole-a.com", "mot_de_passe": "password"})
        res = self.client.get(f"/eleve/{self.eleve_a.id}/modifier")
        html = res.get_data(as_text=True)
        # La classe 6e A doit être sélectionnée
        self.assertIn(f'value="{self.classe_6a.id}" selected', html)

    def test_6_mes_classes_affiche_eleves_via_inscription(self):
        """6. mes_classes affiche les élèves via Inscription et non classe.eleves."""
        self.eleve_a.classe_id = None
        db.session.commit()

        self.client.post("/login", data={"email": "prof1@ecole-a.com", "mot_de_passe": "password"})
        res = self.client.get("/mes_classes")
        html = res.get_data(as_text=True)
        self.assertIn("Amadou", html)
        self.assertIn("Diop", html)

    def test_7_deux_ecoles_differentes_restent_totalement_isolees(self):
        """7. Un prof de l'École A ne peut jamais accéder à un élève de l'École B."""
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(self.user_prof1)
            self.assertFalse(can_access_eleve(self.eleve_b_ecole))

    def test_8_deux_annees_restent_separees(self):
        """8. Les inscriptions de 2025-2026 et 2026-2027 restent strictement séparées."""
        self.assertEqual(self.insc_2025.annee_scolaire_id, self.annee_2025.id)
        self.assertEqual(self.insc_2026.annee_scolaire_id, self.annee_2026.id)
        self.assertNotEqual(self.insc_2025.classe_id, self.insc_2026.classe_id)

    def test_9_sync_mobile_retourne_uniquement_eleves_inscrits(self):
        """9. Endpoint sync mobile retourne uniquement les élèves inscrits dans l'année active."""
        self.eleve_a.classe_id = None
        db.session.commit()

        self.client.post("/login", data={"email": "prof1@ecole-a.com", "mot_de_passe": "password"})
        res = self.client.get("/api/professeur/offline-data")
        data = res.get_json()
        self.assertTrue(data["success"])
        eleves = data.get("eleves", [])
        eleve_ids = [e["id"] for e in eleves]
        self.assertIn(self.eleve_a.id, eleve_ids)

if __name__ == "__main__":
    unittest.main()

