import re
import unittest
from datetime import date, datetime

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Utilisateur,
)
from app.services.annees_scolaires import set_annee_consultee
from app.services.niveaux import ensure_ecole_niveau_configs
from app.services.notes_annuelles import creer_note, get_cours_annee
from app.services.structure_annuelle import sauvegarder_structure_annee


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase5BCorrectionsE2ETestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Ecole
        self.ecole = Ecole(nom="Ecole Pilote")
        db.session.add(self.ecole)
        db.session.commit()

        ensure_ecole_niveau_configs(self.ecole.id, commit=True)
        self.n6 = NiveauScolaire.query.filter_by(code="6E").first()
        self.n5 = NiveauScolaire.query.filter_by(code="5E").first()

        # Année scolaire active
        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.commit()

        # Structure annuelle AnneeNiveauConfig
        sauvegarder_structure_annee(self.ecole.id, self.annee.id, [self.n6.id, self.n5.id])
        db.session.commit()

        # Classes annuelles
        self.classe_6a = Classe(
            nom="6e A",
            niveau="6e",
            niveau_id=self.n6.id if self.n6 else None,
            section="A",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            statut="active",
        )
        self.classe_5a = Classe(
            nom="5e A",
            niveau="5e",
            niveau_id=self.n5.id if self.n5 else None,
            section="A",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            statut="active",
        )
        db.session.add_all([self.classe_6a, self.classe_5a])
        db.session.commit()

        # Admin
        self.admin = Utilisateur(
            nom="Admin",
            prenom="Test",
            email="admin@pilote.local",
            mot_de_passe="secret123",
            role="admin",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.admin)
        db.session.commit()

        # Cours
        self.cours_maths_6a = Cours(nom="Mathématiques 6e", ecole_id=self.ecole.id, classe_id=self.classe_6a.id)
        self.cours_francais_6a = Cours(nom="Français 6e", ecole_id=self.ecole.id, classe_id=self.classe_6a.id)
        self.cours_svt_5a = Cours(nom="SVT 5e", ecole_id=self.ecole.id, classe_id=self.classe_5a.id)
        self.cours_anglais_5a = Cours(nom="Anglais 5e", ecole_id=self.ecole.id, classe_id=self.classe_5a.id)
        db.session.add_all([self.cours_maths_6a, self.cours_francais_6a, self.cours_svt_5a, self.cours_anglais_5a])
        db.session.commit()

        # Élèves et Inscriptions annuelles
        self.eleve_6a = Eleve(nom="Martin", prenom="Alice", genre="F", date_naissance=date(2014, 5, 10), ecole_id=self.ecole.id, statut="actif")
        self.eleve_5a = Eleve(nom="Traore", prenom="Bob", genre="M", date_naissance=date(2013, 8, 20), ecole_id=self.ecole.id, statut="actif")
        db.session.add_all([self.eleve_6a, self.eleve_5a])
        db.session.commit()

        self.ins_6a = Inscription(
            eleve_id=self.eleve_6a.id,
            classe_id=self.classe_6a.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="actif",
        )
        self.ins_5a = Inscription(
            eleve_id=self.eleve_5a.id,
            classe_id=self.classe_5a.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add_all([self.ins_6a, self.ins_5a])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            sess["ecole_id"] = user.ecole_id
        return client

    # =========================================================================
    # TEST A: /eleves avec élèves -> pas d'état vide
    # =========================================================================
    def test_a_eleves_avec_eleves_pas_detat_vide(self):
        """Vérifie que lorsque des élèves sont inscrits, /eleves affiche les élèves et JAMAIS le message d'état vide."""
        client = self.login_as(self.admin)
        response = client.get("/eleves")
        self.assertEqual(response.status_code, 200)

        html = response.data.decode("utf-8")
        # Les élèves doivent être affichés
        self.assertIn("Alice", html)
        self.assertIn("Martin", html)
        self.assertIn("Bob", html)
        self.assertIn("Traore", html)

        # L'état vide NE DOIT PAS être affiché
        self.assertNotIn("Aucun élève inscrit", html)
        self.assertNotIn("Inscrire un premier élève", html)

    # =========================================================================
    # TEST B: /eleves sans élèves -> état vide présent
    # =========================================================================
    def test_b_eleves_sans_eleves_etat_vide_present(self):
        """Vérifie que lorsqu'aucun élève n'est inscrit, /eleves affiche le message d'état vide."""
        # Supprimer les inscriptions et élèves pour cette école
        Inscription.query.filter_by(ecole_id=self.ecole.id).delete()
        Eleve.query.filter_by(ecole_id=self.ecole.id).delete()
        db.session.commit()

        client = self.login_as(self.admin)
        response = client.get("/eleves")
        self.assertEqual(response.status_code, 200)

        html = response.data.decode("utf-8")
        # L'état vide DOIT être affiché
        self.assertIn("Aucun élève inscrit", html)
        self.assertIn("Inscrire un premier élève", html)
        self.assertTrue("Aucun élève n&#39;est inscrit" in html or "Aucun élève n'est inscrit" in html)

    # =========================================================================
    # TEST C: élève en 6e A -> seuls les cours de 6e A sont disponibles
    # =========================================================================
    def test_c_eleve_6a_seuls_cours_6a_disponibles(self):
        """Vérifie que les data-classe-id sur /notes associent Alice à 6e A et que seuls les cours de 6e A correspondent."""
        client = self.login_as(self.admin)
        response = client.get("/notes")
        self.assertEqual(response.status_code, 200)

        html = response.data.decode("utf-8")

        # Option Alice Martin avec data-classe-id de 6e A
        pattern_eleve = rf'<option\s+value="{self.eleve_6a.id}"\s+data-classe-id="{self.classe_6a.id}"'
        self.assertRegex(html, pattern_eleve)

        # Scoper uniquement sur le select #cours_id
        cours_select_match = re.search(r'<select[^>]+id="cours_id"[^>]*>(.*?)</select>', html, re.DOTALL)
        self.assertIsNotNone(cours_select_match)
        cours_html = cours_select_match.group(1)

        # Options cours 6e A avec data-classe-id de 6e A
        pattern_maths = rf'<option\s+value="{self.cours_maths_6a.id}"\s+data-classe-id="{self.classe_6a.id}"'
        self.assertRegex(cours_html, pattern_maths)

        pattern_francais = rf'<option\s+value="{self.cours_francais_6a.id}"\s+data-classe-id="{self.classe_6a.id}"'
        self.assertRegex(cours_html, pattern_francais)

        cours_matches = re.findall(r'<option\s+value="(\d+)"\s+data-classe-id="(\d+)"[^>]*>\s*([^<]+)\s*</option>', cours_html)
        cours_compatibles_alice = [m for m in cours_matches if m[1] == str(self.classe_6a.id)]
        self.assertEqual(len(cours_compatibles_alice), 2)
        noms_compatibles = [m[2] for m in cours_compatibles_alice]
        self.assertTrue(any("Mathématiques 6e" in nom for nom in noms_compatibles))
        self.assertTrue(any("Français 6e" in nom for nom in noms_compatibles))
        self.assertFalse(any("SVT 5e" in nom for nom in noms_compatibles))
        self.assertFalse(any("Anglais 5e" in nom for nom in noms_compatibles))

    # =========================================================================
    # TEST D: élève en 5e A -> seuls les cours de 5e A sont disponibles
    # =========================================================================
    def test_d_eleve_5a_seuls_cours_5a_disponibles(self):
        """Vérifie que les data-classe-id sur /notes associent Bob à 5e A et que seuls les cours de 5e A correspondent."""
        client = self.login_as(self.admin)
        response = client.get("/notes")
        self.assertEqual(response.status_code, 200)

        html = response.data.decode("utf-8")

        # Option élève Bob Traore avec data-classe-id de 5e A
        pattern_eleve = rf'<option\s+value="{self.eleve_5a.id}"\s+data-classe-id="{self.classe_5a.id}"'
        self.assertRegex(html, pattern_eleve)

        # Scoper uniquement sur le select #cours_id
        cours_select_match = re.search(r'<select[^>]+id="cours_id"[^>]*>(.*?)</select>', html, re.DOTALL)
        self.assertIsNotNone(cours_select_match)
        cours_html = cours_select_match.group(1)

        # Options cours 5e A avec data-classe-id de 5e A
        pattern_svt = rf'<option\s+value="{self.cours_svt_5a.id}"\s+data-classe-id="{self.classe_5a.id}"'
        self.assertRegex(cours_html, pattern_svt)

        pattern_anglais = rf'<option\s+value="{self.cours_anglais_5a.id}"\s+data-classe-id="{self.classe_5a.id}"'
        self.assertRegex(cours_html, pattern_anglais)

        cours_matches = re.findall(r'<option\s+value="(\d+)"\s+data-classe-id="(\d+)"[^>]*>\s*([^<]+)\s*</option>', cours_html)
        cours_compatibles_bob = [m for m in cours_matches if m[1] == str(self.classe_5a.id)]
        self.assertEqual(len(cours_compatibles_bob), 2)
        noms_compatibles = [m[2] for m in cours_compatibles_bob]
        self.assertTrue(any("SVT 5e" in nom for nom in noms_compatibles))
        self.assertTrue(any("Anglais 5e" in nom for nom in noms_compatibles))
        self.assertFalse(any("Mathématiques 6e" in nom for nom in noms_compatibles))
        self.assertFalse(any("Français 6e" in nom for nom in noms_compatibles))

    # =========================================================================
    # TEST E: POST forgé note avec élève 6e A et cours 5e A -> refusé par backend
    # =========================================================================
    def test_e_post_forge_eleve_6a_cours_5a_refuse_par_backend(self):
        """Vérifie qu'un POST forgé associant un élève de 6e A avec un cours de 5e A est strictement refusé par le backend."""
        client = self.login_as(self.admin)

        # Tentative d'insertion forgée : Alice (6e A) avec cours SVT 5e (5e A)
        response = client.post(
            "/notes",
            data={
                "eleve_id": self.eleve_6a.id,
                "cours_id": self.cours_svt_5a.id,
                "valeur": "15.0",
                "coefficient": "1.0",
                "type_evaluation": "Devoir",
                "periode": "Trimestre 1",
                "annee_id": self.annee.id,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        html = response.data.decode("utf-8")
        # Le message de refus strict doit apparaître
        self.assertTrue(
            "Cet élève n&#39;est pas inscrit dans la classe de ce cours pour cette année scolaire." in html
            or "Cet élève n'est pas inscrit dans la classe de ce cours pour cette année scolaire." in html
        )

        # Aucune note ne doit avoir été créée en base
        note_en_base = Note.query.filter_by(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_6a.id,
            cours_id=self.cours_svt_5a.id,
        ).first()
        self.assertIsNone(note_en_base)

    # =========================================================================
    # TEST BONUS : POST légitime élève 6e A et cours 6e A -> accepté
    # =========================================================================
    def test_post_legitime_eleve_6a_cours_6a_accepte(self):
        """Vérifie qu'un POST valide (Alice 6e A + Maths 6e A) est accepté et inséré avec succès."""
        client = self.login_as(self.admin)

        response = client.post(
            "/notes",
            data={
                "eleve_id": self.eleve_6a.id,
                "cours_id": self.cours_maths_6a.id,
                "valeur": "16.5",
                "coefficient": "2.0",
                "type_evaluation": "Devoir",
                "periode": "Trimestre 1",
                "annee_id": self.annee.id,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        html = response.data.decode("utf-8")
        self.assertIn("Note ajoutée avec succès", html)

        note_en_base = Note.query.filter_by(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve_6a.id,
            cours_id=self.cours_maths_6a.id,
        ).first()
        self.assertIsNotNone(note_en_base)
        self.assertEqual(note_en_base.valeur, 16.5)
        self.assertEqual(note_en_base.coefficient, 2.0)


if __name__ == "__main__":
    unittest.main()
