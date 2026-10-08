import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Utilisateur, Ecole, AnneeScolaire, Classe, Cours, Eleve, Inscription, Professeur, Note


class ProfPlanATestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-plan-a-prof"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestProfesseurPlanA(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ProfPlanATestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Établissement & Année active
        self.ecole = Ecole(nom="École Test Plan A")
        db.session.add(self.ecole)
        db.session.commit()

        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 15),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        # Compte Professeur
        self.user_prof = Utilisateur(
            nom="DIOP",
            prenom="Moussa",
            email="moussa.prof@test.com",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Secret123!")
        )
        db.session.add(self.user_prof)
        db.session.commit()

        self.prof = Professeur(
            nom="DIOP",
            prenom="Moussa",
            email="moussa.prof@test.com",
            utilisateur_id=self.user_prof.id,
            ecole_id=self.ecole.id
        )
        db.session.add(self.prof)
        db.session.commit()

        # Classe & Cours
        self.classe = Classe(nom="6ème A", niveau="6ème", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        db.session.add(self.classe)
        db.session.commit()

        self.cours = Cours(
            nom="Mathématiques",
            classe_id=self.classe.id,
            professeur_id=self.prof.id,
            ecole_id=self.ecole.id,
            coefficient=2.0
        )
        db.session.add(self.cours)
        db.session.commit()

        # Élève & Inscription
        self.eleve = Eleve(
            nom="Sow",
            prenom="Awa",
            date_naissance=date(2012, 5, 14),
            genre="F",
            code_parent="MAT-001",
            ecole_id=self.ecole.id
        )
        db.session.add(self.eleve)
        db.session.commit()

        self.inscription = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="inscrit"
        )
        db.session.add(self.inscription)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_prof(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_prof.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole.id
            sess['annee_consultee'] = {str(self.ecole.id): self.annee.id}

    def test_navigation_professeur_exactement_trois_liens(self):
        """Vérifie que la barre de navigation du professeur expose exactement les 3 pôles du Plan A."""
        self.login_prof()
        resp = self.client.get('/professeur/dashboard')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # 3 Pôles présents
        self.assertIn("Mon Espace", html)
        self.assertIn("Mes Classes &amp; Présences", html)
        self.assertIn("Mes Évaluations", html)

        # Les anciens liens redondants sont supprimés du menu prof
        self.assertNotIn("Mes enseignements", html)
        self.assertNotIn("Emploi du temps</a>", html)

    def test_redirection_professeur_home_vers_dashboard(self):
        """Vérifie que /professeur redirige proprement vers /professeur/dashboard sans 404."""
        self.login_prof()
        resp = self.client.get('/professeur', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/professeur/dashboard', resp.headers.get('Location', ''))

    def test_mes_enseignements_page_classes_et_presences(self):
        """Vérifie que /mes_enseignements charge les classes et intègre le modal de liste d'élèves."""
        self.login_prof()
        resp = self.client.get('/mes_enseignements')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Mes Classes &amp; Présences", html)
        self.assertIn("6ème A", html)
        self.assertIn("modalElevesClasse", html)
        self.assertIn("ouvrirModalElevesClasse", html)

    def test_api_eleves_par_classe_pour_professeur(self):
        """Vérifie que l'API /api/eleves/classe/<id> renvoie la liste des élèves avec matricule."""
        self.login_prof()
        resp = self.client.get(f'/api/eleves/classe/{self.classe.id}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        self.assertIn('eleves', data)
        self.assertEqual(len(data['eleves']), 1)
        self.assertEqual(data['eleves'][0]['nom'], "Sow")
        self.assertEqual(data['eleves'][0]['prenom'], "Awa")
        self.assertEqual(data['eleves'][0]['matricule'], self.eleve.matricule)
        self.assertRegex(data['eleves'][0]['matricule'], r"^[0-9]{2}-[0-9]{4}$")
        self.assertNotEqual(data['eleves'][0]['matricule'], self.eleve.code_parent)
        self.assertNotIn('code_parent', data['eleves'][0])
        self.assertNotIn(self.eleve.code_parent, resp.get_data(as_text=True))

    def test_zero_fuite_admin_dans_notes_professeur(self):
        """Vérifie l'absence absolue de fuite de données admin dans la page notes pour le professeur."""
        self.login_prof()
        resp = self.client.get('/notes')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Pas de bouton d'incomplet audit admin
        self.assertNotIn("btnFilterIncomplets", html)
        self.assertNotIn("incomplet", html.lower())

        # Modal moderne d'ajout de note présent
        self.assertIn("modalAjouterNote", html)
        self.assertIn("btnSaisieNotesClasse", html)
        self.assertIn("modalSaisieNotesClasseRapide", html)

    def test_saisie_notes_classe_page_responsive(self):
        """Vérifie que la page /notes/saisie_classe charge la nouvelle interface responsive et sticky."""
        self.login_prof()
        resp = self.client.get(f'/notes/saisie_classe?classe_id={self.classe.id}&cours_id={self.cours.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Saisie Rapide des Notes", html)
        self.assertIn("selectClasse", html)
        self.assertIn("selectCours", html)
        self.assertIn("sticky-save-bar", html)
        self.assertIn("btnSubmitNotes", html)
        self.assertIn("eleve-saisie-item", html)
        self.assertIn("inputmode=\"decimal\"", html)

    def test_saisie_notes_classe_ajax_post(self):
        """Vérifie que /notes/saisie_classe accepte les requêtes JSON/AJAX et enregistre les notes."""
        self.login_prof()
        payload = {
            "classe_id": self.classe.id,
            "cours_id": self.cours.id,
            "periode": "Semestre 1",
            "type_evaluation": "Devoir",
            "coefficient": 1.5,
            "notes": {
                str(self.eleve.id): "16.5"
            }
        }
        resp = self.client.post(
            '/notes/saisie_classe',
            json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("nb_notes"), 1)

        # Vérifier que la note a été persistée
        note = Note.query.filter_by(eleve_id=self.eleve.id, cours_id=self.cours.id).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.valeur, 16.5)

    def test_faire_appel_page_responsive(self):
        """Vérifie que la page /absences/appel affiche le nouveau design réactif et compact."""
        self.login_prof()
        resp = self.client.get(f'/absences/appel?classe_id={self.classe.id}&cours_id={self.cours.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Faire l'appel", html)
        self.assertIn("appelEffectif", html)
        self.assertIn("appelAbsents", html)
        self.assertIn("appelPresents", html)
        self.assertIn("sticky-appel-bar", html)
        self.assertIn("eleve-appel-row", html)
        self.assertIn("btnAucunAbsent", html)

    def test_faire_appel_ajax_post(self):
        """Vérifie que /absences/appel accepte l'enregistrement par requête AJAX/JSON."""
        self.login_prof()
        payload = {
            "absent_inscription_ids": [str(self.inscription.id)]
        }
        resp = self.client.post(
            f'/absences/appel?classe_id={self.classe.id}&cours_id={self.cours.id}',
            json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("absents_count"), 1)

    def test_modal_appel_direct_integre_dashboard_et_classes(self):
        """Vérifie que le modalAppelDirect est intégré au dashboard et à mes_enseignements."""
        self.login_prof()
        resp_dash = self.client.get('/professeur/dashboard')
        self.assertEqual(resp_dash.status_code, 200)
        html_dash = resp_dash.get_data(as_text=True)
        self.assertIn("modalAppelDirect", html_dash)
        self.assertIn("ouvrirModalAppelDirect", html_dash)

        resp_classes = self.client.get('/mes_enseignements')
        self.assertEqual(resp_classes.status_code, 200)
        html_classes = resp_classes.get_data(as_text=True)
        self.assertIn("modalAppelDirect", html_classes)
        self.assertIn("ouvrirModalAppelDirect", html_classes)


if __name__ == '__main__':
    unittest.main()
