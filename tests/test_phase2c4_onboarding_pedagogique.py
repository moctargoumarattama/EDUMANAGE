import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    EcoleNiveauConfig,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from app.services.niveaux import ensure_standard_niveaux
from app.services.structure_annuelle import get_niveaux_catalogue_grouped_for_onboarding
from app.utils import get_school_setup_state


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2C4OnboardingPedagogiqueTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        ensure_standard_niveaux(commit=True)

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.prof = Utilisateur(nom="Prof", email="prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        db.session.add_all([self.admin, self.prof, self.parent])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_as(self, user):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["role"] = user.role
            session["ecole_id"] = user.ecole_id
        return client

    def create_active_year(self, ecole=None):
        ecole = ecole or self.ecole_a
        annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=ecole.id,
        )
        db.session.add(annee)
        db.session.commit()
        return annee

    def niveau_ids(self, codes):
        return [
            NiveauScolaire.query.filter_by(code=code).first().id
            for code in codes
        ]

    def test_admin_sans_annee_active_reste_etape_annee(self):
        state = get_school_setup_state(self.ecole_a.id, force_refresh=True)
        self.assertFalse(state["setup_complete"])
        self.assertEqual(state["current_step"], "year")
        response = self.login_as(self.admin).get("/onboarding")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"creer_annee", response.data)

    def test_annee_active_sans_config_affiche_etape_pedagogique_sans_creer_config(self):
        self.create_active_year()
        state = get_school_setup_state(self.ecole_a.id, force_refresh=True)
        self.assertFalse(state["setup_complete"])
        self.assertEqual(state["current_step"], "pedagogie")
        self.assertEqual(AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id).count(), 0)

        response = self.login_as(self.admin).get("/onboarding")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"configurer_pedagogie", response.data)
        self.assertEqual(AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id).count(), 0)

    def test_selection_college_persiste_uniquement_niveaux_choisis_et_termine_sans_classe(self):
        annee = self.create_active_year()
        client = self.login_as(self.admin)
        selected = self.niveau_ids(["6E", "5E", "4E"])
        response = client.post("/onboarding", data={
            "action": "configurer_pedagogie",
            "niveau_ids": [str(niveau_id) for niveau_id in selected],
        })
        self.assertEqual(response.status_code, 302)

        actifs = {
            config.niveau.code
            for config in AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=annee.id, actif=True).all()
        }
        self.assertEqual(actifs, {"6E", "5E", "4E"})
        self.assertNotIn("3E", actifs)
        self.assertFalse(AnneeNiveauConfig.query.join(NiveauScolaire).filter(AnneeNiveauConfig.ecole_id == self.ecole_a.id, NiveauScolaire.cycle == "primaire", AnneeNiveauConfig.actif.is_(True)).first())
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id).count(), 0)
        self.assertEqual(Cours.query.filter_by(ecole_id=self.ecole_a.id).count(), 0)
        self.assertEqual(Inscription.query.filter_by(ecole_id=self.ecole_a.id).count(), 0)

        state = get_school_setup_state(self.ecole_a.id, force_refresh=True)
        self.assertTrue(state["setup_complete"])
        self.assertEqual(state["current_step"], "complete")

    def test_validation_oblige_un_niveau_et_ne_modifie_pas_partiellement(self):
        self.create_active_year()
        response = self.login_as(self.admin).post("/onboarding", data={"action": "configurer_pedagogie"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id).count(), 0)
        self.assertFalse(get_school_setup_state(self.ecole_a.id, force_refresh=True)["setup_complete"])

    def test_multi_ecoles_et_prechargement_config_existante(self):
        annee_a = self.create_active_year(self.ecole_a)
        annee_b = self.create_active_year(self.ecole_b)
        sixieme_id = self.niveau_ids(["6E"])[0]
        ci_id = self.niveau_ids(["CI"])[0]
        db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_b.id, annee_scolaire_id=annee_b.id, niveau_id=ci_id, actif=True))
        db.session.commit()

        self.login_as(self.admin).post("/onboarding", data={
            "action": "configurer_pedagogie",
            "niveau_ids": [str(sixieme_id)],
        })
        actifs_a = {config.niveau.code for config in AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=annee_a.id, actif=True).all()}
        actifs_b = {config.niveau.code for config in AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_b.id, annee_scolaire_id=annee_b.id, actif=True).all()}
        self.assertEqual(actifs_a, {"6E"})
        self.assertEqual(actifs_b, {"CI"})

        options = get_niveaux_catalogue_grouped_for_onboarding(self.ecole_b.id, annee_b.id)
        self.assertTrue(next(item for item in options["primaire"] if item.niveau.code == "CI").actif)

    def test_professeur_parent_refuses(self):
        self.create_active_year()
        self.assertNotEqual(self.login_as(self.prof).get("/onboarding").status_code, 200)
        self.assertNotEqual(self.login_as(self.parent).get("/onboarding").status_code, 200)

    def test_ancienne_ecole_avec_classe_continue_sans_exiger_classe_pour_nouvelle(self):
        annee = self.create_active_year()
        niveau_id = self.niveau_ids(["6E"])[0]
        db.session.add(AnneeNiveauConfig(ecole_id=self.ecole_a.id, annee_scolaire_id=annee.id, niveau_id=niveau_id, actif=True))
        db.session.add(Classe(nom="6e A", niveau="6e", niveau_id=niveau_id, annee_scolaire_id=annee.id, ecole_id=self.ecole_a.id))
        db.session.commit()

        state = get_school_setup_state(self.ecole_a.id, force_refresh=True)
        self.assertTrue(state["setup_complete"])
        response = self.login_as(self.admin).get("/")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
