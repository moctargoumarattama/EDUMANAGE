import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Utilisateur, Ecole, AnneeScolaire, Professeur, PointagePersonnel, FichePaiePersonnel


class PaiePersonnelTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-paie"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestPaiePersonnel(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PaiePersonnelTestConfig)
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

        # Utilisateur Professeur 1 (Horaire)
        self.user_prof_horaire = Utilisateur(
            nom="TOURE",
            prenom="Sekou",
            email="sekou.toure@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof_horaire)
        db.session.commit()

        # Professeur horaire (taux horaire 5 000 FCFA)
        self.prof_horaire = Professeur(
            nom="TOURE",
            prenom="Sekou",
            email="sekou.toure@excellence.edu",
            utilisateur_id=self.user_prof_horaire.id,
            ecole_id=self.ecole.id,
            type_remuneration="horaire",
            salaire_base=0.0,
            taux_horaire=5000.0
        )
        db.session.add(self.prof_horaire)

        # Utilisateur Professeur 2 (Fixe)
        self.user_prof_fixe = Utilisateur(
            nom="KABA",
            prenom="Aissatou",
            email="aissatou.kaba@excellence.edu",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof_fixe)
        db.session.commit()

        # Professeur fixe (salaire de base 250 000 FCFA)
        self.prof_fixe = Professeur(
            nom="KABA",
            prenom="Aissatou",
            email="aissatou.kaba@excellence.edu",
            utilisateur_id=self.user_prof_fixe.id,
            ecole_id=self.ecole.id,
            type_remuneration="fixe",
            salaire_base=250000.0,
            taux_horaire=0.0
        )
        db.session.add(self.prof_fixe)
        db.session.commit()

        # Création de pointages pour prof_horaire (20 heures au total en Octobre 2025)
        # 2 jours de 8 heures + 1 jour de 4 heures = 20 heures
        pt1 = PointagePersonnel(
            professeur_id=self.prof_horaire.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 6),
            statut='present',
            heures_effectuees=8.0,
            valide=True
        )
        pt2 = PointagePersonnel(
            professeur_id=self.prof_horaire.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 7),
            statut='present',
            heures_effectuees=8.0,
            valide=True
        )
        pt3 = PointagePersonnel(
            professeur_id=self.prof_horaire.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            date_pointage=date(2025, 10, 8),
            statut='present',
            heures_effectuees=4.0,
            valide=True
        )
        db.session.add_all([pt1, pt2, pt3])
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
            sess['_user_id'] = str(self.user_prof_horaire.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole.id
            sess['role'] = 'professeur'
            sess['annee_consultee'] = {str(self.ecole.id): self.annee.id}

    def test_securite_acces_paie(self):
        """1. Sécurité : accès interdit aux professeurs, autorisé aux administrateurs."""
        # Non connecté -> redirection login
        resp_anon = self.client.get('/paie-personnel/')
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn('/login', resp_anon.headers.get('Location', ''))

        # Connecté en tant que Professeur -> accès interdit (302 ou 403)
        self.login_prof()
        resp_prof = self.client.get('/paie-personnel/')
        self.assertIn(resp_prof.status_code, (302, 403))

        # Connecté en tant qu'Admin -> 200 OK
        self.login_admin()
        resp_admin = self.client.get('/paie-personnel/?mois=10&annee=2025')
        self.assertEqual(resp_admin.status_code, 200)
        self.assertIn("Paie du Personnel", resp_admin.get_data(as_text=True))

    def test_calcul_exact_professeur_horaire(self):
        """2. Calcul exact pour professeur horaire : 20 heures x 5 000 FCFA = 100 000 FCFA."""
        self.login_admin()

        resp = self.client.post(
            '/paie-personnel/calculer-mois',
            json={"mois": 10, "annee": 2025},
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

        fiche_h = FichePaiePersonnel.query.filter_by(
            professeur_id=self.prof_horaire.id,
            mois=10,
            annee=2025
        ).first()

        self.assertIsNotNone(fiche_h)
        self.assertEqual(fiche_h.type_remuneration, "horaire")
        self.assertEqual(fiche_h.heures_travaillees, 20.0)
        self.assertEqual(fiche_h.taux_horaire, 5000.0)
        # 20.0 * 5000 = 100 000 FCFA
        self.assertEqual(fiche_h.salaire_brut, 100000.0)
        self.assertEqual(fiche_h.net_a_payer, 100000.0)
        self.assertEqual(fiche_h.statut_paiement, "en_attente")

    def test_calcul_exact_professeur_fixe(self):
        """3. Calcul exact pour professeur fixe : salaire fixe 250 000 FCFA."""
        self.login_admin()

        resp = self.client.post(
            '/paie-personnel/calculer-mois',
            json={"mois": 10, "annee": 2025},
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)

        fiche_f = FichePaiePersonnel.query.filter_by(
            professeur_id=self.prof_fixe.id,
            mois=10,
            annee=2025
        ).first()

        self.assertIsNotNone(fiche_f)
        self.assertEqual(fiche_f.type_remuneration, "fixe")
        self.assertEqual(fiche_f.salaire_base, 250000.0)
        self.assertEqual(fiche_f.salaire_brut, 250000.0)
        self.assertEqual(fiche_f.net_a_payer, 250000.0)
        self.assertEqual(fiche_f.statut_paiement, "en_attente")

    def test_enregistrement_reglement_partiel_et_total(self):
        """4. Enregistrement d'un règlement partiel et d'un règlement total avec bascule des statuts."""
        self.login_admin()

        # Calculer d'abord le mois
        self.client.post('/paie-personnel/calculer-mois', json={"mois": 10, "annee": 2025})
        fiche_h = FichePaiePersonnel.query.filter_by(
            professeur_id=self.prof_horaire.id,
            mois=10,
            annee=2025
        ).first()

        # 1. Paiement partiel : verser 40 000 FCFA sur 100 000 FCFA
        resp_partiel = self.client.post(
            '/paie-personnel/enregistrer-reglement',
            json={
                "fiche_id": fiche_h.id,
                "montant_verse": 40000.0,
                "mode_reglement": "especes",
                "reference_recu": "REC-PART-001"
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp_partiel.status_code, 200)
        data_p = resp_partiel.get_json()
        self.assertTrue(data_p["success"])
        self.assertEqual(data_p["fiche"]["statut_paiement"], "partiel")
        self.assertEqual(data_p["fiche"]["montant_paye"], 40000.0)
        self.assertEqual(data_p["fiche"]["reste_a_payer"], 60000.0)

        # 2. Paiement total : verser le solde complet (100 000 FCFA)
        resp_total = self.client.post(
            '/paie-personnel/enregistrer-reglement',
            json={
                "fiche_id": fiche_h.id,
                "montant_verse": 100000.0,
                "mode_reglement": "virement",
                "reference_recu": "VIR-TOT-001"
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp_total.status_code, 200)
        data_t = resp_total.get_json()
        self.assertTrue(data_t["success"])
        self.assertEqual(data_t["fiche"]["statut_paiement"], "paye")
        self.assertEqual(data_t["fiche"]["reste_a_payer"], 0.0)

    def test_ajustement_primes_et_retenues_recalcul_net(self):
        """5. Ajustement des primes et déductions avec recalcul immédiat du net."""
        self.login_admin()

        self.client.post('/paie-personnel/calculer-mois', json={"mois": 10, "annee": 2025})
        fiche_f = FichePaiePersonnel.query.filter_by(
            professeur_id=self.prof_fixe.id,
            mois=10,
            annee=2025
        ).first()

        # Salaire fixe de base : 250 000 FCFA
        # Ajout Prime = 30 000 FCFA, Retenue = 10 000 FCFA
        # Nouveau Net = 250 000 + 30 000 - 10 000 = 270 000 FCFA
        resp = self.client.post(
            '/paie-personnel/ajuster-ligne',
            json={
                "fiche_id": fiche_f.id,
                "primes": 30000.0,
                "retenues": 10000.0,
                "commentaire": "Prime de fin de trimestre - Avance sur salaire"
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["fiche"]["primes"], 30000.0)
        self.assertEqual(data["fiche"]["deductions"], 10000.0)
        self.assertEqual(data["fiche"]["net_a_payer"], 270000.0)
        self.assertEqual(data["fiche"]["note"], "Prime de fin de trimestre - Avance sur salaire")

    def test_configuration_contrat_professeur(self):
        """6. Modification dynamique du contrat d'un professeur."""
        self.login_admin()

        # Basculer le prof horaire en prof fixe avec 300 000 FCFA
        resp = self.client.post(
            '/paie-personnel/configurer-contrat',
            json={
                "professeur_id": self.prof_horaire.id,
                "type_remuneration": "fixe",
                "salaire_base": 300000.0,
                "taux_horaire": 0.0
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

        # Vérification en base
        db.session.refresh(self.prof_horaire)
        self.assertEqual(self.prof_horaire.type_remuneration, "fixe")
        self.assertEqual(self.prof_horaire.salaire_base, 300000.0)

    def test_rendu_route_impression_bulletin(self):
        """7. Rendu 200 de la route d'impression officielle du bulletin de paie."""
        self.login_admin()

        self.client.post('/paie-personnel/calculer-mois', json={"mois": 10, "annee": 2025})
        fiche = FichePaiePersonnel.query.filter_by(
            professeur_id=self.prof_horaire.id,
            mois=10,
            annee=2025
        ).first()

        resp = self.client.get(f'/paie-personnel/bulletin/{fiche.id}/print')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("BULLETIN DE PAIE", html)
        self.assertIn(self.prof_horaire.nom, html)
        self.assertIn("100 000", html)
    def test_rattachement_annee_scolaire_active(self):
        """8. Vérification stricte du rattachement de la paie à l'année scolaire active."""
        self.login_admin()

        # Accès avec période de l'année active
        resp = self.client.get('/paie-personnel/?periode=2025-10')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("2025-2026", html)
        self.assertIn("Octobre 2025", html)

        # Vérifier que les mois proposés contiennent les mois de l'année (Septembre 2025, Juin 2026)
        self.assertIn("Septembre 2025", html)
        self.assertIn("Juin 2026", html)

        # Création d'une autre année scolaire et d'un pointage sur cette autre année
        autre_annee = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="terminee",
            ecole_id=self.ecole.id
        )
        db.session.add(autre_annee)
        db.session.commit()

        # Pointage pour prof_horaire en Octobre sur l'ancienne année
        pt_ancien = PointagePersonnel(
            professeur_id=self.prof_horaire.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=autre_annee.id,
            date_pointage=date(2025, 10, 20),
            statut='present',
            heures_effectuees=10.0,
            valide=True
        )
        db.session.add(pt_ancien)
        db.session.commit()

        # Calculer le mois d'octobre 2025 sur l'année active
        resp_calc = self.client.post('/paie-personnel/calculer-mois', json={"mois": 10, "annee": 2025})
        self.assertEqual(resp_calc.status_code, 200)

        # La fiche calculée ne doit comptabiliser QUE les heures de l'année active (20h) et PAS les 10h de l'autre année
        fiche = FichePaiePersonnel.query.filter_by(
            professeur_id=self.prof_horaire.id,
            annee_scolaire_id=self.annee.id,
            mois=10,
            annee=2025
        ).first()
        self.assertIsNotNone(fiche)
        self.assertEqual(fiche.heures_travaillees, 20.0)
        self.assertEqual(fiche.salaire_brut, 100000.0)


if __name__ == '__main__':
    unittest.main()

