import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Utilisateur, Ecole, AnneeScolaire, Professeur, PointagePersonnel


class PointagePersonnelTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-pointage"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestPointagePersonnel(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PointagePersonnelTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Établissement (avec onboarding_complete=True pour éviter redirection middleware)
        self.ecole = Ecole(nom="Groupe Scolaire Excellence", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.commit()

        # Année scolaire
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        # Admin
        self.user_admin = Utilisateur(
            nom="Directeur",
            prenom="Mamadou",
            email="directeur@excellence.edu",
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Admin1234!")
        )
        db.session.add(self.user_admin)
        db.session.commit()

        # Professeur 1
        self.user_prof1 = Utilisateur(
            nom="DIALLO",
            prenom="Ousmane",
            email="ousmane.diallo@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof1)
        db.session.commit()

        self.prof1 = Professeur(
            nom="DIALLO",
            prenom="Ousmane",
            email="ousmane.diallo@excellence.edu",
            utilisateur_id=self.user_prof1.id,
            ecole_id=self.ecole.id,
            salaire_base=250000.0
        )
        db.session.add(self.prof1)

        # Professeur 2 (Vacataire)
        self.user_prof2 = Utilisateur(
            nom="SOW",
            prenom="Mariam",
            email="mariam.sow@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof2)
        db.session.commit()

        self.prof2 = Professeur(
            nom="SOW",
            prenom="Mariam",
            email="mariam.sow@excellence.edu",
            utilisateur_id=self.user_prof2.id,
            ecole_id=self.ecole.id,
            taux_horaire=5000.0
        )
        db.session.add(self.prof2)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_admin.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole.id
            sess['role'] = 'admin'
            sess['onboarding_complete'] = True
            sess['onboarding_complete_' + str(self.ecole.id)] = True
            sess['annee_consultee'] = {str(self.ecole.id): self.annee.id}

    def login_prof(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_prof1.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole.id
            sess['role'] = 'professeur'
            sess['annee_consultee'] = {str(self.ecole.id): self.annee.id}

    def test_acces_securise_pointage(self):
        """1. Vérifie le contrôle d'accès : refus pour professeur, autorisé pour admin."""
        # Non connecté -> redirection login
        resp_anon = self.client.get('/pointage-personnel/')
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn('/login', resp_anon.headers.get('Location', ''))

        # Connecté en tant que Professeur -> accès interdit (403 ou redirection)
        self.login_prof()
        resp_prof = self.client.get('/pointage-personnel/')
        self.assertIn(resp_prof.status_code, (302, 403))

        # Connexion en tant qu'Admin -> 200 OK
        self.login_admin()
        resp_admin = self.client.get('/pointage-personnel/')
        self.assertEqual(resp_admin.status_code, 200)
        html = resp_admin.get_data(as_text=True)
        self.assertIn("Pointage du Personnel", html)
        self.assertIn("Tout marquer présent", html)

    def test_marquer_tous_presents(self):
        """2. Vérifie l'action de masse AJAX marquant tous les professeurs présents."""
        self.login_admin()
        payload = {
            "date": "2025-10-10",
            "creneau_debut": "08:00",
            "creneau_fin": "16:00"
        }
        resp = self.client.post(
            '/pointage-personnel/marquer-tous-presents',
            json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

        # Vérification en base de données
        pointages = PointagePersonnel.query.filter_by(date_pointage=date(2025, 10, 10)).all()
        self.assertEqual(len(pointages), 2)
        for p in pointages:
            self.assertEqual(p.statut, 'present')
            self.assertEqual(p.retard_minutes, 0)
            self.assertEqual(p.heures_effectuees, 8.0)
            self.assertEqual(p.pointe_par_id, self.user_admin.id)

    def test_mise_a_jour_statut_retard_avec_minutes(self):
        """3. Vérifie l'enregistrement d'un statut en retard avec minutes et déduction dynamique."""
        self.login_admin()
        payload = {
            "professeur_id": self.prof1.id,
            "date": "2025-10-10",
            "statut": "retard",
            "retard_minutes": 30,
            "creneau_debut": "08:00",
            "creneau_fin": "16:00",
            "motif": "Problème de transport"
        }
        resp = self.client.post(
            '/pointage-personnel/sauvegarder-ligne',
            json=payload,
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        # 8h - 0.5h = 7.5h
        self.assertEqual(data.get("heures_effectuees"), 7.5)

        # Vérification en base
        p = PointagePersonnel.query.filter_by(
            professeur_id=self.prof1.id,
            date_pointage=date(2025, 10, 10)
        ).first()
        self.assertIsNotNone(p)
        self.assertEqual(p.statut, 'retard')
        self.assertEqual(p.retard_minutes, 30)
        self.assertEqual(p.heures_effectuees, 7.5)
        self.assertEqual(p.motif, "Problème de transport")

    def test_rejet_statut_invalide_ou_conge(self):
        """4. Vérifie l'impossibilité d'injecter un statut invalide ou un statut 'conge'."""
        self.login_admin()

        # Tentative avec statut 'conge' -> DOIT ÊTRE REJETÉ (400)
        resp_conge = self.client.post(
            '/pointage-personnel/sauvegarder-ligne',
            json={
                "professeur_id": self.prof1.id,
                "date": "2025-10-10",
                "statut": "conge"
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp_conge.status_code, 400)
        data_conge = resp_conge.get_json()
        self.assertFalse(data_conge.get("success"))
        self.assertIn("non autorisé", data_conge.get("error", "").lower())

        # Tentative avec statut fantaisiste 'vacances' -> 400
        resp_inv = self.client.post(
            '/pointage-personnel/sauvegarder-ligne',
            json={
                "professeur_id": self.prof1.id,
                "date": "2025-10-10",
                "statut": "vacances"
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp_inv.status_code, 400)

        # S'assurer qu'aucun pointage 'conge' n'a été inséré
        count_conge = PointagePersonnel.query.filter_by(statut='conge').count()
        self.assertEqual(count_conge, 0)

    def test_cloture_journee_validation(self):
        """5. Vérifie la clôture et le scellement de la journée via validation."""
        self.login_admin()

        # Marquer d'abord les présences
        self.client.post(
            '/pointage-personnel/marquer-tous-presents',
            json={"date": "2025-10-10"},
            headers={"X-Requested-With": "XMLHttpRequest"}
        )

        # Clôturer la journée
        resp_val = self.client.post(
            '/pointage-personnel/valider-journee',
            data={"date": "2025-10-10"},
            follow_redirects=True
        )
        self.assertEqual(resp_val.status_code, 200)

        # Vérification en base
        pointages = PointagePersonnel.query.filter_by(date_pointage=date(2025, 10, 10)).all()
        self.assertTrue(len(pointages) > 0)
        for p in pointages:
            self.assertTrue(p.valide)
            self.assertEqual(p.valide_par_user_id, self.user_admin.id)
            self.assertIsNotNone(p.date_validation)

    def test_rapport_mensuel_api(self):
        """6. Vérifie le calcul agrégé pour le rapport mensuel."""
        self.login_admin()

        # Ajouter un pointage présent pour prof1 et un retard pour prof2
        p1 = PointagePersonnel(
            professeur_id=self.prof1.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 1),
            statut='present',
            heures_effectuees=8.0,
            valide=True
        )
        p2 = PointagePersonnel(
            professeur_id=self.prof2.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 1),
            statut='retard',
            retard_minutes=20,
            heures_effectuees=7.67,
            valide=True
        )
        db.session.add_all([p1, p2])
        db.session.commit()

        resp = self.client.get(
            '/pointage-personnel/rapport-mensuel?mois=10&annee=2025',
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("kpis", data)
        self.assertGreater(data["kpis"]["total_heures"], 15.0)
        self.assertEqual(data["kpis"]["total_retards_minutes"], 20)
        self.assertEqual(len(data["professeurs"]), 2)

    def test_modal_pointage_professeur_dashboard(self):
        """7. Vérifie que l'espace professeur contient le modal popup sans nouvelle page."""
        self.login_prof()
        resp = self.client.get('/professeur/dashboard')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # Vérifie la présence du modal et de son bouton déclencheur
        self.assertIn("modalPointageProf", html)
        self.assertIn("Mon Assiduité", html)


if __name__ == '__main__':
    unittest.main()
