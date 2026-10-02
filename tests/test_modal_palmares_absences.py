import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import Absence, AnneeScolaire, Classe, Ecole, Eleve, Inscription, Utilisateur
from app.services.absences_annuelles import get_palmares_absences_annuel


class PalmaresTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-palmares-absences-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestModalPalmaresAbsences(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PalmaresTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        self.ecole = Ecole(
            nom="École Palmarès Test",
            statut="actif",
            onboarding_complete=True,
        )
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

        self.admin = Utilisateur(
            email="admin_palmares@test.com",
            nom="Admin",
            prenom="Directeur",
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        self.admin.mot_de_passe = generate_password_hash("AdminPass123!")
        db.session.add(self.admin)
        db.session.flush()

        # Création de plusieurs classes et élèves
        self.classes = []
        for i, nom in enumerate(["6ème A", "6ème B", "5ème A", "5ème B", "4ème A", "3ème A"]):
            cl = Classe(
                nom=nom,
                niveau="Collège",
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee.id,
            )
            db.session.add(cl)
            self.classes.append(cl)
        db.session.flush()

        # Inscriptions et Absences
        # 6ème A : 3 absences (1 justifiée, 2 non justifiées)
        # 6ème B : 10 absences (5 justifiées, 5 non justifiées) -> la plus touchée
        # 5ème A : 0 absence -> la plus assidue
        self.eleves = []
        for idx, cl in enumerate(self.classes):
            e = Eleve(
                nom=f"Nom{idx}",
                prenom=f"Prenom{idx}",
                date_naissance=date(2012, 1, 1),
                ecole_id=self.ecole.id,
            )
            db.session.add(e)
            db.session.flush()
            ins = Inscription(
                eleve_id=e.id,
                classe_id=cl.id,
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee.id,
                statut="inscrit",
            )
            db.session.add(ins)
            db.session.flush()

            # Ajouter absences selon la classe
            if cl.nom == "6ème B":
                for k in range(10):
                    abs_obj = Absence(
                        eleve_id=e.id,
                        inscription_id=ins.id,
                        ecole_id=self.ecole.id,
                        date_absence=date(2026, 10, 1),
                        justifiee=(k % 2 == 0),
                    )
                    db.session.add(abs_obj)
            elif cl.nom == "6ème A":
                for k in range(3):
                    abs_obj = Absence(
                        eleve_id=e.id,
                        inscription_id=ins.id,
                        ecole_id=self.ecole.id,
                        date_absence=date(2026, 10, 1),
                        justifiee=(k == 0),
                    )
                    db.session.add(abs_obj)
            elif cl.nom == "4ème A":
                abs_obj = Absence(
                    eleve_id=e.id,
                    inscription_id=ins.id,
                    ecole_id=self.ecole.id,
                    date_absence=date(2026, 10, 2),
                    justifiee=True,
                )
                db.session.add(abs_obj)

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_admin(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True

    def test_get_palmares_absences_annuel_service(self):
        palmares = get_palmares_absences_annuel(self.ecole.id, self.annee)

        # Vérifier structure
        self.assertIn("classes_plus_touchees", palmares)
        self.assertIn("classes_plus_assidues", palmares)
        self.assertIn("classe_plus_absente", palmares)
        self.assertIn("classe_plus_assidue", palmares)
        self.assertIn("statistiques", palmares)
        self.assertIn("chart_data", palmares)

        # Classe la plus touchée doit être 6ème B avec 10 absences
        self.assertIsNotNone(palmares["classe_plus_absente"])
        self.assertEqual(palmares["classe_plus_absente"]["nom"], "6ème B")
        self.assertEqual(palmares["classe_plus_absente"]["total_absences"], 10)

        # Classe la plus assidue doit avoir 0 absence
        self.assertIsNotNone(palmares["classe_plus_assidue"])
        self.assertEqual(palmares["classe_plus_assidue"]["total_absences"], 0)

        # Total absences globales = 10 + 3 + 1 = 14
        stats = palmares["statistiques"]
        self.assertEqual(stats["total_absences"], 14)
        self.assertEqual(stats["total_classes"], 6)

        # Top 5 classes les plus touchées
        self.assertGreaterEqual(len(palmares["classes_plus_touchees"]), 1)
        self.assertEqual(palmares["classes_plus_touchees"][0]["nom"], "6ème B")

    def test_api_absences_palmares(self):
        self.login_admin()
        resp = self.client.get("/api/absences/palmares")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["statistiques"]["total_absences"], 14)
        self.assertEqual(data["classe_plus_absente"]["nom"], "6ème B")

    def test_absences_html_contains_modal_and_button(self):
        self.login_admin()
        resp = self.client.get("/absences")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Bouton d'ouverture du modal
        self.assertIn('id="btnOpenPalmaresAbsences"', html)
        self.assertIn("modalPalmaresAbsences", html)

        # Contenu du modal
        self.assertIn("Palmarès &amp; Alertes", html)
        self.assertIn("Top 5 - Classes les plus touchées", html)
        self.assertIn("Top 5 - Classes les plus assidues", html)
        self.assertIn('id="chartPalmaresAbsences"', html)
        self.assertIn("modal_palmares_absences.js", html)
