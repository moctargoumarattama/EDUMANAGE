import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import AnneeScolaire, Classe, Cours, Ecole, Eleve, Inscription, Note, Utilisateur
from app.services.notes_annuelles import get_palmares_notes_annuel


class PalmaresNotesTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-palmares-notes-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestModalPalmaresNotes(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PalmaresNotesTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        self.ecole = Ecole(
            nom="École Palmarès Notes Test",
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
            email="admin_notes@test.com",
            nom="Admin",
            prenom="Directeur",
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        self.admin.mot_de_passe = generate_password_hash("AdminPass123!")
        db.session.add(self.admin)
        db.session.flush()

        # Création de 6 classes
        self.classes = []
        for nom in ["6ème A", "6ème B", "5ème A", "5ème B", "4ème A", "3ème A"]:
            cl = Classe(
                nom=nom,
                niveau="Collège",
                ecole_id=self.ecole.id,
                annee_scolaire_id=self.annee.id,
            )
            db.session.add(cl)
            self.classes.append(cl)
        db.session.flush()

        # Élèves et Cours pour chaque classe
        # 6ème A : moyenne 16.0 (Excellence)
        # 6ème B : moyenne 8.0 (Suivi / Faible)
        # 5ème A : moyenne 12.0
        # 5ème B : moyenne 14.0
        # 4ème A : moyenne 10.0
        # 3ème A : moyenne 11.0
        notes_par_classe = {
            "6ème A": [16.0, 16.0],
            "6ème B": [8.0, 8.0],
            "5ème A": [12.0, 12.0],
            "5ème B": [14.0, 14.0],
            "4ème A": [10.0, 10.0],
            "3ème A": [11.0, 11.0],
        }

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

            cours = Cours(
                nom=f"Maths {cl.nom}",
                classe_id=cl.id,
                ecole_id=self.ecole.id,
                coefficient=1.0,
            )
            db.session.add(cours)
            db.session.flush()

            for note_val in notes_par_classe.get(cl.nom, [10.0]):
                n = Note(
                    valeur=note_val,
                    coefficient=1.0,
                    eleve_id=e.id,
                    inscription_id=ins.id,
                    cours_id=cours.id,
                    ecole_id=self.ecole.id,
                    annee_id=self.annee.id,
                    date_evaluation=date(2026, 10, 1),
                    type_evaluation="Devoir",
                    periode="Semestre 1",
                )
                db.session.add(n)

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_admin(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True

    def test_get_palmares_notes_annuel_service(self):
        palmares = get_palmares_notes_annuel(self.ecole.id, self.annee)

        # Vérifier structure
        self.assertIn("classes_meilleures", palmares)
        self.assertIn("classes_faibles", palmares)
        self.assertIn("classe_meilleure", palmares)
        self.assertIn("classe_plus_faible", palmares)
        self.assertIn("statistiques", palmares)
        self.assertIn("chart_data", palmares)

        # Classe la meilleure = 6ème A avec 16.0/20
        self.assertIsNotNone(palmares["classe_meilleure"])
        self.assertEqual(palmares["classe_meilleure"]["nom"], "6ème A")
        self.assertEqual(palmares["classe_meilleure"]["moyenne"], 16.0)

        # Classe la plus faible = 6ème B avec 8.0/20
        self.assertIsNotNone(palmares["classe_plus_faible"])
        self.assertEqual(palmares["classe_plus_faible"]["nom"], "6ème B")
        self.assertEqual(palmares["classe_plus_faible"]["moyenne"], 8.0)

        # Top 5 des meilleures
        self.assertEqual(len(palmares["classes_meilleures"]), 5)
        self.assertEqual(palmares["classes_meilleures"][0]["nom"], "6ème A")

        # Top 5 des faibles
        self.assertEqual(len(palmares["classes_faibles"]), 5)
        self.assertEqual(palmares["classes_faibles"][0]["nom"], "6ème B")

        # Statistiques
        stats = palmares["statistiques"]
        self.assertEqual(stats["total_notes"], 12)
        self.assertEqual(stats["total_classes_evaluees"], 6)

    def test_api_notes_palmares(self):
        self.login_admin()
        resp = self.client.get("/api/notes/palmares")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["classe_meilleure"]["nom"], "6ème A")
        self.assertEqual(data["classe_plus_faible"]["nom"], "6ème B")

    def test_notes_html_contains_modal_and_button(self):
        self.login_admin()
        resp = self.client.get("/notes")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Bouton
        self.assertIn('id="btnOpenPalmaresNotes"', html)
        self.assertIn("modalPalmaresNotes", html)

        # Contenu
        self.assertIn("Palmarès Académique &amp; Performances", html)
        self.assertIn("Top 5 - Meilleures Moyennes", html)
        self.assertIn("Top 5 - Suivi Pédagogique", html)
        self.assertIn('id="chartPalmaresNotes"', html)
        self.assertIn("modal_palmares_notes.js", html)
