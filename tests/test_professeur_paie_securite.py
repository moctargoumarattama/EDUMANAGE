import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Utilisateur, Ecole, AnneeScolaire, Professeur, FichePaiePersonnel


class ProfesseurPaieSecuriteTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-prof-paie-securite"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestProfesseurPaieSecurite(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ProfesseurPaieSecuriteTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Établissement
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

        # 1. Admin
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

        # 2. Super Admin (exclu de la consultation des fiches de paie internes selon la règle métier)
        self.user_super_admin = Utilisateur(
            nom="Super",
            prenom="Admin",
            email="superadmin@excellence.edu",
            role="super_admin",
            statut="actif",
            mot_de_passe=generate_password_hash("SuperAdmin1234!")
        )
        db.session.add(self.user_super_admin)

        # 3. Professeur A
        self.user_profA = Utilisateur(
            nom="CAMARA",
            prenom="Ibrahima",
            email="ibrahima.camara@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_profA)
        db.session.commit()

        self.profA = Professeur(
            nom="CAMARA",
            prenom="Ibrahima",
            email="ibrahima.camara@excellence.edu",
            utilisateur_id=self.user_profA.id,
            ecole_id=self.ecole.id,
            type_remuneration="fixe",
            salaire_base=250000.0,
            taux_horaire=0.0
        )
        db.session.add(self.profA)

        # 4. Professeur B
        self.user_profB = Utilisateur(
            nom="KOUNDOUNO",
            prenom="Fanta",
            email="fanta.koundouno@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_profB)
        db.session.commit()

        self.profB = Professeur(
            nom="KOUNDOUNO",
            prenom="Fanta",
            email="fanta.koundouno@excellence.edu",
            utilisateur_id=self.user_profB.id,
            ecole_id=self.ecole.id,
            type_remuneration="horaire",
            salaire_base=0.0,
            taux_horaire=6000.0
        )
        db.session.add(self.profB)

        # 5. Utilisateur Parent
        self.user_parent = Utilisateur(
            nom="Parent",
            prenom="Aliou",
            email="parent.aliou@excellence.edu",
            role="parent",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Parent1234!")
        )
        db.session.add(self.user_parent)
        db.session.commit()

        # Fiche de paie de Professeur A (Octobre 2025)
        self.ficheA = FichePaiePersonnel(
            professeur_id=self.profA.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025",
            type_remuneration="fixe",
            salaire_base=250000.0,
            taux_horaire=0.0,
            heures_travaillees=40.0,
            salaire_brut=250000.0,
            salaire_net=250000.0,
            statut_paiement="paye",
            montant_paye=250000.0
        )
        db.session.add(self.ficheA)

        # Fiche de paie de Professeur B (Octobre 2025)
        self.ficheB = FichePaiePersonnel(
            professeur_id=self.profB.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025",
            type_remuneration="horaire",
            salaire_base=0.0,
            taux_horaire=6000.0,
            heures_travaillees=30.0,
            salaire_brut=180000.0,
            salaire_net=180000.0,
            statut_paiement="partiel",
            montant_paye=100000.0
        )
        db.session.add(self.ficheB)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_as(self, user):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            sess['role'] = user.role
            if user.ecole_id:
                sess['ecole_id'] = user.ecole_id
                sess['annee_consultee'] = {str(user.ecole_id): self.annee.id}
                sess['onboarding_complete'] = True
                sess['onboarding_complete_' + str(user.ecole_id)] = True

    def test_acces_dashboard_professeur_et_modal_paie(self):
        """1. Accès réussi : Le Professeur A ouvre son tableau de bord et voit sa modale de rémunération."""
        self.login_as(self.user_profA)

        resp = self.client.get('/professeur/dashboard')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Présence du bouton et de la modale
        self.assertIn("Ma Rémunération", html)
        self.assertIn("modalPaieProfesseur", html)
        self.assertIn("Mes Bulletins &amp; Rémunérations", html)
        # Présence du montant de sa fiche
        self.assertIn("250 000", html)

    def test_telechargement_propre_bulletin_succes(self):
        """2. Téléchargement réussi : Le Professeur A peut afficher son propre bulletin de paie HTTP 200."""
        self.login_as(self.user_profA)

        resp = self.client.get(f'/paie-personnel/bulletin/{self.ficheA.id}/print')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("BULLETIN DE PAIE", html)
        self.assertIn("CAMARA", html)
        self.assertIn("Ibrahima", html)
        self.assertIn("250 000", html)

    def test_etancheite_stricte_tentative_usurpation_bloquee(self):
        """3. ÉTANCHÉITÉ STRICTE : Le Professeur A tente d'accéder au bulletin de Professeur B -> 403 FORBIDDEN."""
        self.login_as(self.user_profA)

        # Tentative d'accès à la fiche B
        resp = self.client.get(f'/paie-personnel/bulletin/{self.ficheB.id}/print')
        self.assertEqual(resp.status_code, 403, "Une tentative de piratage inter-professeurs doit retourner 403 Forbidden")

    def test_roles_non_autorises_refuses(self):
        """4. Les rôles non autorisés (super_admin, parent, non-authentifié) sont strictement rejetés."""
        # 1. Super Admin (exclu expressément de la consultation directe des fiches de paie internes)
        self.login_as(self.user_super_admin)
        with self.client.session_transaction() as sess:
            sess['ecole_id'] = self.ecole.id
        resp_sa = self.client.get(f'/paie-personnel/bulletin/{self.ficheA.id}/print')
        self.assertEqual(resp_sa.status_code, 403)

        # 2. Parent d'élève -> 403 ou redirection
        self.login_as(self.user_parent)
        resp_parent = self.client.get(f'/paie-personnel/bulletin/{self.ficheA.id}/print')
        self.assertIn(resp_parent.status_code, (302, 403))

        # 3. Utilisateur Anonyme (déconnecté) -> redirection vers login
        with self.client.session_transaction() as sess:
            sess.clear()
        resp_anon = self.client.get(f'/paie-personnel/bulletin/{self.ficheA.id}/print')
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn('/login', resp_anon.headers.get('Location', ''))

    def test_api_mes_fiches_paie_isolee_par_professeur(self):
        """5. L'API mes fiches retourne uniquement les fiches du professeur connecté."""
        self.login_as(self.user_profA)

        resp = self.client.get('/professeur/mes-fiches-paie')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

        fiche_ids = [f["id"] for f in data.get("fiches", [])]
        self.assertIn(self.ficheA.id, fiche_ids)
        self.assertNotIn(self.ficheB.id, fiche_ids, "Le professeur A ne doit jamais voir les fiches du professeur B dans sa liste")

    def test_administrateur_peut_consulter_tous_bulletins_ecole(self):
        """6. L'administrateur de l'école peut consulter le bulletin de A et le bulletin de B."""
        self.login_as(self.user_admin)

        respA = self.client.get(f'/paie-personnel/bulletin/{self.ficheA.id}/print')
        self.assertEqual(respA.status_code, 200)

        respB = self.client.get(f'/paie-personnel/bulletin/{self.ficheB.id}/print')
        self.assertEqual(respB.status_code, 200)


if __name__ == '__main__':
    unittest.main()

