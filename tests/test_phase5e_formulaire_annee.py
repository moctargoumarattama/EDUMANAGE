import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Ecole,
    NiveauScolaire,
    Utilisateur,
)
from app.services.annees_scolaires import (
    construire_nom_annee,
    valider_dates_annee,
    valider_unicite_annee,
    MESSAGE_ANNEE_COURTE_INVALIDE,
    MESSAGE_DATE_FIN_ANTERIEURE,
    MESSAGE_ANNEE_ARCHIVEE_MODIF,
)
from app.services.niveaux import ensure_standard_niveaux


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase5EFormulaireAnneeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        ensure_standard_niveaux(commit=True)

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        self.ecole_onboarding = Ecole(nom="Ecole Onboarding")
        db.session.add_all([self.ecole_a, self.ecole_b, self.ecole_onboarding])
        db.session.flush()

        self.super_admin = Utilisateur(
            nom="SuperAdmin", email="super@test.local", mot_de_passe="x", role="super_admin", ecole_id=None
        )
        self.admin_a = Utilisateur(
            nom="AdminA", email="admin_a@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id
        )
        self.admin_b = Utilisateur(
            nom="AdminB", email="admin_b@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_b.id
        )
        self.admin_onboarding = Utilisateur(
            nom="AdminOnb", email="admin_onb@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_onboarding.id
        )
        db.session.add_all([self.super_admin, self.admin_a, self.admin_b, self.admin_onboarding])
        db.session.commit()

        # Configurer l'école A et B comme ayant terminé l'onboarding pour accéder directement à /annees
        self._completer_setup_ecole(self.ecole_a)
        self._completer_setup_ecole(self.ecole_b)

    def _completer_setup_ecole(self, ecole):
        annee_initiale = AnneeScolaire(
            nom="2023-2024",
            date_debut=date(2023, 9, 1),
            date_fin=date(2024, 6, 30),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add(annee_initiale)
        db.session.flush()
        for niv in NiveauScolaire.query.all():
            db.session.add(
                AnneeNiveauConfig(
                    ecole_id=ecole.id,
                    annee_scolaire_id=annee_initiale.id,
                    niveau_id=niv.id,
                    actif=True,
                )
            )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user, ecole_id=None):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            sess["role"] = user.role
            sess["ecole_id"] = ecole_id if ecole_id is not None else user.ecole_id
        return client

    # 1. 26 -> 2026-2027
    def test_01_construire_nom_annee_26(self):
        nom, debut, fin, err = construire_nom_annee("26")
        self.assertIsNone(err)
        self.assertEqual(nom, "2026-2027")
        self.assertEqual(debut, 2026)
        self.assertEqual(fin, 2027)

    # 2. 27 -> 2027-2028
    def test_02_construire_nom_annee_27(self):
        nom, debut, fin, err = construire_nom_annee("27")
        self.assertIsNone(err)
        self.assertEqual(nom, "2027-2028")
        self.assertEqual(debut, 2027)
        self.assertEqual(fin, 2028)

    # 3. 29 -> 2029-2030
    def test_03_construire_nom_annee_29(self):
        nom, debut, fin, err = construire_nom_annee("29")
        self.assertIsNone(err)
        self.assertEqual(nom, "2029-2030")
        self.assertEqual(debut, 2029)
        self.assertEqual(fin, 2030)

    # 4. 30 -> 2030-2031
    def test_04_construire_nom_annee_30(self):
        nom, debut, fin, err = construire_nom_annee("30")
        self.assertIsNone(err)
        self.assertEqual(nom, "2030-2031")
        self.assertEqual(debut, 2030)
        self.assertEqual(fin, 2031)

    # 5. 2026 -> refus
    def test_05_refus_2026(self):
        nom, debut, fin, err = construire_nom_annee("2026")
        self.assertIsNotNone(err)
        self.assertEqual(err, MESSAGE_ANNEE_COURTE_INVALIDE)
        self.assertIsNone(nom)

    # 6. abc -> refus
    def test_06_refus_abc(self):
        nom, debut, fin, err = construire_nom_annee("abc")
        self.assertIsNotNone(err)
        self.assertEqual(err, MESSAGE_ANNEE_COURTE_INVALIDE)
        self.assertIsNone(nom)

    # 7. vide ou formats invalides -> refus
    def test_07_refus_vide_ou_invalides(self):
        for val in ["", None, "2A", "-1", "2", "   "]:
            nom, debut, fin, err = construire_nom_annee(val)
            self.assertIsNotNone(err, f"La valeur '{val}' devrait être refusée")
            self.assertEqual(err, MESSAGE_ANNEE_COURTE_INVALIDE)
            self.assertIsNone(nom)

    # 8. POST nom trafiqué impossible (le backend ignore le champ nom libre)
    def test_08_post_nom_trafique_ignore(self):
        client = self.login_as(self.admin_a)
        response = client.post(
            "/annees",
            data={
                "action": "ajouter",
                "annee_debut_court": "26",
                "nom": "nom_arbitraire_ou_2026-2040",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
                "ecole_id": str(self.ecole_a.id),
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        annee = AnneeScolaire.query.filter_by(ecole_id=self.ecole_a.id, nom="2026-2027").first()
        self.assertIsNotNone(annee)
        self.assertEqual(annee.nom, "2026-2027")
        self.assertIsNone(AnneeScolaire.query.filter_by(nom="nom_arbitraire_ou_2026-2040").first())

        # Tentative d'envoyer uniquement un nom libre sans les 2 chiffres valides
        response2 = client.post(
            "/annees",
            data={
                "action": "ajouter",
                "nom": "2028-2029",
                "annee_debut_court": "invalide",
                "date_debut": "2028-09-01",
                "date_fin": "2029-06-30",
                "ecole_id": str(self.ecole_a.id),
            },
            follow_redirects=True,
        )
        self.assertIn(MESSAGE_ANNEE_COURTE_INVALIDE.encode("utf-8"), response2.data)
        self.assertIsNone(AnneeScolaire.query.filter_by(nom="2028-2029").first())

    # 9. Date début 2026 + Fin 2027 -> valide pour 2026-2027
    def test_09_dates_coherentes_valides(self):
        d_debut = date(2026, 9, 15)
        d_fin = date(2027, 6, 30)
        ok, err = valider_dates_annee(d_debut, d_fin, 2026, 2027)
        self.assertTrue(ok)
        self.assertIsNone(err)

    # 10. Date début 2025 -> refus pour 2026-2027
    def test_10_date_debut_incoherente_refusee(self):
        d_debut = date(2025, 9, 15)
        d_fin = date(2027, 6, 30)
        ok, err = valider_dates_annee(d_debut, d_fin, 2026, 2027)
        self.assertFalse(ok)
        self.assertIn("La date de début doit être dans l'année 2026", err)

    # 11. Date fin 2028 -> refus pour 2026-2027
    def test_11_date_fin_incoherente_refusee(self):
        d_debut = date(2026, 9, 15)
        d_fin = date(2028, 6, 30)
        ok, err = valider_dates_annee(d_debut, d_fin, 2026, 2027)
        self.assertFalse(ok)
        self.assertIn("La date de fin doit être dans l'année 2027", err)

    # 12. date_fin <= date_debut -> refus
    def test_12_date_fin_anterieure_refusee(self):
        d_debut = date(2026, 9, 15)
        d_fin = date(2026, 9, 10)
        ok, err = valider_dates_annee(d_debut, d_fin, 2026, 2026)
        self.assertFalse(ok)
        self.assertEqual(err, MESSAGE_DATE_FIN_ANTERIEURE)

    # 13. Doublon 2026-2027 dans la même école -> refus
    def test_13_doublon_meme_ecole_refuse(self):
        client = self.login_as(self.admin_a)
        # Première création 2026-2027
        client.post(
            "/annees",
            data={
                "action": "ajouter",
                "annee_debut_court": "26",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
                "ecole_id": str(self.ecole_a.id),
            },
            follow_redirects=True,
        )
        self.assertEqual(AnneeScolaire.query.filter_by(ecole_id=self.ecole_a.id, nom="2026-2027").count(), 1)

        # Deuxième création avec le même nom pour la même école
        response = client.post(
            "/annees",
            data={
                "action": "ajouter",
                "annee_debut_court": "26",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
                "ecole_id": str(self.ecole_a.id),
            },
            follow_redirects=True,
        )
        self.assertEqual(AnneeScolaire.query.filter_by(ecole_id=self.ecole_a.id, nom="2026-2027").count(), 1)
        self.assertIn("existe déjà".encode("utf-8"), response.data)

    # 14. 2026-2027 dans deux écoles différentes -> autorisé
    def test_14_meme_annee_deux_ecoles_autorisee(self):
        client_a = self.login_as(self.admin_a)
        res_a = client_a.post(
            "/annees",
            data={
                "action": "ajouter",
                "annee_debut_court": "26",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
                "ecole_id": str(self.ecole_a.id),
            },
            follow_redirects=True,
        )
        self.assertEqual(res_a.status_code, 200)

        client_b = self.login_as(self.admin_b)
        res_b = client_b.post(
            "/annees",
            data={
                "action": "ajouter",
                "annee_debut_court": "26",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
                "ecole_id": str(self.ecole_b.id),
            },
            follow_redirects=True,
        )
        self.assertEqual(res_b.status_code, 200)

        annee_a = AnneeScolaire.query.filter_by(ecole_id=self.ecole_a.id, nom="2026-2027").first()
        annee_b = AnneeScolaire.query.filter_by(ecole_id=self.ecole_b.id, nom="2026-2027").first()
        self.assertIsNotNone(annee_a)
        self.assertIsNotNone(annee_b)
        self.assertNotEqual(annee_a.id, annee_b.id)

    # 15. Onboarding utilise la même génération et validation
    def test_15_onboarding_generation_et_validation(self):
        client = self.login_as(self.admin_onboarding)

        # 1) Tentative avec valeur courte invalide
        response_invalide = client.post(
            "/onboarding",
            data={
                "action": "creer_annee",
                "annee_debut_court": "abc",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
            },
            follow_redirects=True,
        )
        self.assertIn(MESSAGE_ANNEE_COURTE_INVALIDE.encode("utf-8"), response_invalide.data)
        self.assertEqual(AnneeScolaire.query.filter_by(ecole_id=self.ecole_onboarding.id).count(), 0)

        # 2) Saisie valide lors de l'onboarding avec champ nom trafiqué ignoré
        response = client.post(
            "/onboarding",
            data={
                "action": "creer_annee",
                "annee_debut_court": "26",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
                "nom": "trafique_onboarding",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        annee = AnneeScolaire.query.filter_by(ecole_id=self.ecole_onboarding.id).first()
        self.assertIsNotNone(annee)
        self.assertEqual(annee.nom, "2026-2027")
        self.assertNotEqual(annee.nom, "trafique_onboarding")
        self.assertEqual(annee.statut, "active")

    # 16. Route /annees (ajouter et modifier)
    def test_16_route_annees_ajouter_et_modifier(self):
        client = self.login_as(self.admin_a)
        # Créer 26 -> 2026-2027
        client.post(
            "/annees",
            data={
                "action": "ajouter",
                "annee_debut_court": "26",
                "date_debut": "2026-09-01",
                "date_fin": "2027-06-30",
                "ecole_id": str(self.ecole_a.id),
            },
            follow_redirects=True,
        )
        annee = AnneeScolaire.query.filter_by(ecole_id=self.ecole_a.id, nom="2026-2027").first()
        self.assertIsNotNone(annee)

        # Modifier via action='modifier' vers 27 -> 2027-2028
        response_mod = client.post(
            "/annees",
            data={
                "action": "modifier",
                "annee_id": str(annee.id),
                "annee_debut_court": "27",
                "date_debut": "2027-09-01",
                "date_fin": "2028-06-30",
            },
            follow_redirects=True,
        )
        self.assertEqual(response_mod.status_code, 200)
        db.session.refresh(annee)
        self.assertEqual(annee.nom, "2027-2028")
        self.assertEqual(annee.date_debut, date(2027, 9, 1))
        self.assertEqual(annee.date_fin, date(2028, 6, 30))

        # Modifier via route dédiée /annees/<id>/modifier vers 28 -> 2028-2029
        response_route = client.post(
            f"/annees/{annee.id}/modifier",
            data={
                "annee_debut_court": "28",
                "date_debut": "2028-09-01",
                "date_fin": "2029-06-30",
            },
            follow_redirects=True,
        )
        self.assertEqual(response_route.status_code, 200)
        db.session.refresh(annee)
        self.assertEqual(annee.nom, "2028-2029")

    # 17. Année archivée protégée
    def test_17_annee_archivee_protegee(self):
        annee_arch = AnneeScolaire(
            nom="2021-2022",
            date_debut=date(2021, 9, 1),
            date_fin=date(2022, 6, 30),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(annee_arch)
        db.session.commit()

        client = self.login_as(self.admin_a)
        response = client.post(
            "/annees",
            data={
                "action": "modifier",
                "annee_id": str(annee_arch.id),
                "annee_debut_court": "22",
                "date_debut": "2022-09-01",
                "date_fin": "2023-06-30",
            },
            follow_redirects=True,
        )
        self.assertIn(MESSAGE_ANNEE_ARCHIVEE_MODIF.encode("utf-8"), response.data)
        db.session.refresh(annee_arch)
        self.assertEqual(annee_arch.nom, "2021-2022")

        # Tentative via route directe
        response_route = client.post(
            f"/annees/{annee_arch.id}/modifier",
            data={
                "annee_debut_court": "22",
                "date_debut": "2022-09-01",
                "date_fin": "2023-06-30",
            },
            follow_redirects=True,
        )
        self.assertIn(MESSAGE_ANNEE_ARCHIVEE_MODIF.encode("utf-8"), response_route.data)
        db.session.refresh(annee_arch)
        self.assertEqual(annee_arch.nom, "2021-2022")


if __name__ == "__main__":
    unittest.main()

