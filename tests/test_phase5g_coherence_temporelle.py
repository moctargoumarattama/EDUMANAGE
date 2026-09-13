import unittest
from datetime import date, time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    EmploiTemps,
    NiveauScolaire,
    Professeur,
    Utilisateur,
)
from app.services.annees_scolaires import construire_nom_annee, valider_dates_annee
from app.services.coherence_temporelle import (
    intervalles_se_chevauchent,
    valider_intervalle_heures,
)
from app.services.emploi_temps_annuel import (
    detecter_conflits,
    valider_et_creer_creneau,
    valider_et_modifier_creneau,
)


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5g"
    LOGIN_DISABLED = False


class Phase5GCoherenceTemporelleTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole Temps")
        self.ecole.setup_step = "complete"
        self.ecole.setup_complete = True
        db.session.add(self.ecole)
        db.session.flush()

        self.admin = Utilisateur(
            nom="Admin",
            email="admin.phase5g@test.local",
            mot_de_passe="test",
            role="admin",
            ecole_id=self.ecole.id,
        )
        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.admin, self.annee])
        db.session.flush()

        self.niveau = NiveauScolaire(code="6E", nom="6e", cycle="college", ordre=1)
        db.session.add(self.niveau)
        db.session.flush()
        db.session.add(AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            niveau_id=self.niveau.id,
            actif=True,
        ))
        db.session.flush()

        self.classe_a = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        self.classe_b = Classe(nom="5e A", niveau="5e", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        db.session.add_all([self.classe_a, self.classe_b])
        db.session.flush()

        self.prof_user = Utilisateur(
            nom="ProfUser",
            email="prof.phase5g@test.local",
            mot_de_passe="test",
            role="professeur",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.prof_user)
        db.session.flush()

        self.prof = Professeur(
            nom="Dupont",
            prenom="Paul",
            specialite="Math",
            ecole_id=self.ecole.id,
            utilisateur_id=self.prof_user.id,
        )
        db.session.add(self.prof)
        db.session.flush()

        self.cours_a = Cours(
            nom="Math",
            coefficient=1,
            ecole_id=self.ecole.id,
            classe_id=self.classe_a.id,
            professeur_id=self.prof.id,
        )
        self.cours_b = Cours(
            nom="Francais",
            coefficient=1,
            ecole_id=self.ecole.id,
            classe_id=self.classe_b.id,
            professeur_id=self.prof.id,
        )
        self.cours_a2 = Cours(
            nom="Histoire",
            coefficient=1,
            ecole_id=self.ecole.id,
            classe_id=self.classe_a.id,
            professeur_id=self.prof.id,
        )
        db.session.add_all([self.cours_a, self.cours_b, self.cours_a2])
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login_admin(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True
            sess["role"] = "admin"
            sess["ecole_id"] = self.ecole.id

    def test_01_annees_valides(self):
        for saisie, attendu in [("2026-2027", "2026-2027"), ("2027-2028", "2027-2028")]:
            with self.subTest(saisie=saisie):
                nom, debut, fin, err = construire_nom_annee(saisie)
                self.assertIsNone(err)
                self.assertEqual(nom, attendu)
                self.assertEqual(fin, debut + 1)

    def test_02_annees_incoherentes_refusees(self):
        for saisie in ["2028-2027", "2026-2030", "2026-2026"]:
            with self.subTest(saisie=saisie):
                nom, debut, fin, err = construire_nom_annee(saisie)
                self.assertIsNone(nom)
                self.assertIsNone(debut)
                self.assertIsNone(fin)
                self.assertIsNotNone(err)

    def test_03_annee_fin_reconstruite_depuis_debut_court(self):
        nom, debut, fin, err = construire_nom_annee("26")
        self.assertIsNone(err)
        self.assertEqual((nom, debut, fin), ("2026-2027", 2026, 2027))

    def test_04_post_nom_annee_trafique_ne_persiste_pas(self):
        self.login_admin()
        response = self.client.post(
            "/annees",
            data={
                "action": "ajouter",
                "annee_debut_court": "28",
                "annee_fin_court": "27",
                "nom": "2028-2027",
                "ecole_id": str(self.ecole.id),
                "date_debut": "2028-09-01",
                "date_fin": "2029-06-30",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(AnneeScolaire.query.filter_by(nom="2028-2029", ecole_id=self.ecole.id).first())
        self.assertIsNone(AnneeScolaire.query.filter_by(nom="2028-2027", ecole_id=self.ecole.id).first())

    def test_05_dates_annee_controlees(self):
        self.assertEqual(valider_dates_annee(date(2026, 9, 15), date(2027, 6, 30), 2026, 2027), (True, None))
        self.assertFalse(valider_dates_annee(date(2026, 9, 15), date(2026, 6, 30), 2026, 2027)[0])
        self.assertFalse(valider_dates_annee(date(2027, 9, 15), date(2028, 6, 30), 2026, 2027)[0])
        self.assertFalse(valider_dates_annee(date(2026, 9, 15), date(2028, 6, 30), 2026, 2027)[0])

    def test_06_intervalles_heures_positifs_autorises(self):
        for debut, fin in [
            (time(8, 0), time(8, 15)),
            (time(8, 0), time(8, 30)),
            (time(8, 0), time(8, 45)),
            (time(8, 0), time(9, 0)),
            (time(9, 10), time(9, 40)),
            (time(8, 0), time(8, 10)),
        ]:
            with self.subTest(debut=debut, fin=fin):
                self.assertEqual(valider_intervalle_heures(debut, fin), (True, None))

    def test_07_intervalles_heures_incoherents_refuses(self):
        for debut, fin in [
            (time(10, 0), time(8, 0)),
            (time(8, 0), time(8, 0)),
            (time(22, 0), time(1, 0)),
        ]:
            with self.subTest(debut=debut, fin=fin):
                ok, err = valider_intervalle_heures(debut, fin)
                self.assertFalse(ok)
                self.assertIsNotNone(err)

    def test_08_creation_emploi_horaire_incoherent_refusee(self):
        creneau, err = valider_et_creer_creneau(
            self.ecole.id,
            self.annee,
            self.classe_a.id,
            self.cours_a.id,
            self.prof.id,
            "Lundi",
            time(10, 0),
            time(8, 0),
        )
        self.assertIsNone(creneau)
        self.assertIn("fin", err)

    def test_09_creation_emploi_horaire_court_autorisee(self):
        creneau, err = valider_et_creer_creneau(
            self.ecole.id,
            self.annee,
            self.classe_a.id,
            self.cours_a.id,
            self.prof.id,
            "Lundi",
            time(8, 0),
            time(8, 15),
        )
        self.assertIsNone(err)
        self.assertIsNotNone(creneau)

    def test_10_modification_emploi_horaire_incoherent_refusee(self):
        creneau, err = valider_et_creer_creneau(
            self.ecole.id,
            self.annee,
            self.classe_a.id,
            self.cours_a.id,
            self.prof.id,
            "Mardi",
            time(8, 0),
            time(9, 0),
        )
        self.assertIsNone(err)
        modifie, err_modif = valider_et_modifier_creneau(
            self.ecole.id,
            self.annee,
            creneau.id,
            self.classe_a.id,
            self.cours_a.id,
            self.prof.id,
            "Mardi",
            time(8, 0),
            time(7, 30),
        )
        self.assertIsNone(modifie)
        self.assertIn("fin", err_modif)

    def test_11_chevauchement_meme_classe_refuse(self):
        first, err = valider_et_creer_creneau(
            self.ecole.id, self.annee, self.classe_a.id, self.cours_a.id, self.prof.id, "Mercredi", time(8, 0), time(9, 0)
        )
        self.assertIsNone(err)
        has_conflict, msg = detecter_conflits(
            self.ecole.id, self.annee, "Mercredi", time(8, 30), time(9, 30), self.classe_a.id, self.prof.id
        )
        self.assertTrue(has_conflict)
        self.assertIn("classe", msg.lower())

    def test_12_chevauchement_meme_professeur_refuse(self):
        first, err = valider_et_creer_creneau(
            self.ecole.id, self.annee, self.classe_a.id, self.cours_a.id, self.prof.id, "Jeudi", time(8, 0), time(9, 0)
        )
        self.assertIsNone(err)
        second, err_second = valider_et_creer_creneau(
            self.ecole.id, self.annee, self.classe_b.id, self.cours_b.id, self.prof.id, "Jeudi", time(8, 30), time(9, 30)
        )
        self.assertIsNone(second)
        self.assertIn("enseignant", err_second.lower())

    def test_13_creneaux_adjacents_autorises(self):
        c1, e1 = valider_et_creer_creneau(
            self.ecole.id, self.annee, self.classe_a.id, self.cours_a.id, self.prof.id, "Vendredi", time(8, 0), time(8, 30)
        )
        c2, e2 = valider_et_creer_creneau(
            self.ecole.id, self.annee, self.classe_a.id, self.cours_a.id, self.prof.id, "Vendredi", time(8, 30), time(9, 0)
        )
        self.assertIsNone(e1)
        self.assertIsNone(e2)
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)

    def test_14_creneaux_adjacents_longs_autorises(self):
        c1, e1 = valider_et_creer_creneau(
            self.ecole.id, self.annee, self.classe_b.id, self.cours_b.id, self.prof.id, "Samedi", time(8, 0), time(10, 0)
        )
        c2, e2 = valider_et_creer_creneau(
            self.ecole.id, self.annee, self.classe_b.id, self.cours_b.id, self.prof.id, "Samedi", time(10, 0), time(12, 0)
        )
        self.assertIsNone(e1)
        self.assertIsNone(e2)
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)

    def test_15_formule_chevauchement_stricte(self):
        self.assertTrue(intervalles_se_chevauchent(time(8, 0), time(10, 0), time(9, 30), time(11, 0)))
        self.assertFalse(intervalles_se_chevauchent(time(8, 0), time(8, 30), time(8, 30), time(9, 0)))


if __name__ == "__main__":
    unittest.main()
