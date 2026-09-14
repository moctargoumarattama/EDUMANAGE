import unittest
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app, db
from app.models import Absence, AnneeScolaire, Classe, Ecole, Eleve, Inscription, PeriodeBulletin, Presence, Utilisateur
from app.services.bulletins_annuels import calculer_bulletin_data
from app.services.semestres import (
    SEMESTRE_1,
    SEMESTRE_2,
    calculer_bornes_semestres,
    configurer_semestres_annee,
    compter_absences_semestre,
    compter_retards_semestre,
    date_dans_semestre,
    get_semestres_annee,
)


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5h"
    LOGIN_DISABLED = False


class Phase5HSemestresDatesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole Semestres", statut="actif")
        self.autre_ecole = Ecole(nom="Autre Ecole", statut="actif")
        self.ecole.setup_step = "complete"
        self.ecole.setup_complete = True
        self.autre_ecole.setup_step = "complete"
        self.autre_ecole.setup_complete = True
        db.session.add_all([self.ecole, self.autre_ecole])
        db.session.flush()

        self.annee = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 15),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        self.planifiee = AnneeScolaire(
            nom="2027-2028",
            date_debut=date(2027, 9, 15),
            date_fin=date(2028, 6, 30),
            statut="planifiee",
            ecole_id=self.ecole.id,
        )
        self.archivee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 15),
            date_fin=date(2026, 6, 30),
            statut="archivee",
            ecole_id=self.ecole.id,
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 15),
            date_fin=date(2027, 6, 30),
            statut="active",
            ecole_id=self.autre_ecole.id,
        )
        db.session.add_all([self.annee, self.planifiee, self.archivee, self.annee_b])
        db.session.flush()

        self.classe = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        db.session.add(self.classe)
        db.session.flush()

        self.eleve = Eleve(nom="Diallo", prenom="Awa", date_naissance=date(2014, 1, 1), ecole_id=self.ecole.id)
        db.session.add(self.eleve)
        db.session.flush()
        self.inscription = Inscription(
            eleve_id=self.eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
        )
        db.session.add(self.inscription)
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="admin5h@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole.id)
        db.session.add(self.admin)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True
            sess["role"] = "admin"
            sess["ecole_id"] = self.ecole.id
            sess["annee_consultee"] = {str(self.ecole.id): self.annee.id}

    def test_01_bornes_canoniques(self):
        bornes, err = calculer_bornes_semestres(self.annee, date(2027, 1, 31))
        self.assertIsNone(err)
        self.assertEqual(bornes[SEMESTRE_1], (date(2026, 9, 15), date(2027, 1, 31)))
        self.assertEqual(bornes[SEMESTRE_2], (date(2027, 2, 1), date(2027, 6, 30)))
        self.assertEqual(bornes[SEMESTRE_1][0], self.annee.date_debut)
        self.assertEqual(bornes[SEMESTRE_2][1], self.annee.date_fin)
        self.assertEqual((bornes[SEMESTRE_1][1].toordinal() + 1), bornes[SEMESTRE_2][0].toordinal())
        self.assertLess(bornes[SEMESTRE_1][1], bornes[SEMESTRE_2][0])

    def test_02_fin_s1_invalide_refusee(self):
        for fin_s1 in [date(2026, 9, 1), date(2026, 9, 15), date(2027, 6, 30), date(2027, 7, 15)]:
            with self.subTest(fin_s1=fin_s1):
                bornes, err = calculer_bornes_semestres(self.annee, fin_s1)
                self.assertIsNone(bornes)
                self.assertIsNotNone(err)

    def test_03_configuration_planifiee_autorisee_archivee_refusee_autre_ecole_refusee(self):
        periodes, err = configurer_semestres_annee(self.ecole.id, self.planifiee.id, date(2028, 1, 31))
        self.assertIsNone(err)
        self.assertEqual(len(periodes), 2)

        periodes, err = configurer_semestres_annee(self.ecole.id, self.archivee.id, date(2026, 1, 31))
        self.assertIsNone(periodes)
        self.assertIn("lecture seule", err.lower())

        periodes, err = configurer_semestres_annee(self.ecole.id, self.annee_b.id, date(2027, 1, 31))
        self.assertIsNone(periodes)
        self.assertIsNotNone(err)

    def test_04_absences_et_retards_separes_par_semestre(self):
        configurer_semestres_annee(self.ecole.id, self.annee.id, date(2027, 1, 31))
        db.session.add_all([
            Absence(date_absence=date(2027, 1, 31), eleve_id=self.eleve.id, inscription_id=self.inscription.id, ecole_id=self.ecole.id),
            Absence(date_absence=date(2027, 2, 1), eleve_id=self.eleve.id, inscription_id=self.inscription.id, ecole_id=self.ecole.id),
            Absence(date_absence=date(2025, 1, 1), eleve_id=self.eleve.id, ecole_id=self.ecole.id),
            Presence(eleve_id=self.eleve.id, date=date(2027, 1, 30), statut="retard"),
            Presence(eleve_id=self.eleve.id, date=date(2027, 2, 2), statut="retard"),
        ])
        db.session.commit()

        self.assertEqual(compter_absences_semestre(self.ecole.id, self.annee.id, self.inscription.id, SEMESTRE_1), 1)
        self.assertEqual(compter_absences_semestre(self.ecole.id, self.annee.id, self.inscription.id, SEMESTRE_2), 1)
        self.assertEqual(compter_retards_semestre(self.ecole.id, self.annee.id, self.inscription.id, SEMESTRE_1), 0)
        self.assertEqual(compter_retards_semestre(self.ecole.id, self.annee.id, self.inscription.id, SEMESTRE_2), 0)

        s1 = PeriodeBulletin.query.filter_by(ecole_id=self.ecole.id, annee_id=self.annee.id, nom=SEMESTRE_1).first()
        s2 = PeriodeBulletin.query.filter_by(ecole_id=self.ecole.id, annee_id=self.annee.id, nom=SEMESTRE_2).first()
        self.assertTrue(date_dans_semestre(date(2027, 1, 31), s1))
        self.assertTrue(date_dans_semestre(date(2027, 2, 1), s2))

    def test_05_bulletin_stats_semestre_et_calendrier_absent(self):
        data, err = calculer_bulletin_data(self.ecole.id, self.annee, self.inscription, periode=SEMESTRE_1)
        self.assertIsNone(err)
        self.assertIsNone(data["nb_absences"])
        self.assertIsNone(data["nb_retards"])
        self.assertFalse(data["calendrier_semestres_configure"])

        configurer_semestres_annee(self.ecole.id, self.annee.id, date(2027, 1, 31))
        db.session.add_all([
            Absence(date_absence=date(2027, 1, 31), eleve_id=self.eleve.id, inscription_id=self.inscription.id, ecole_id=self.ecole.id),
            Absence(date_absence=date(2027, 2, 1), eleve_id=self.eleve.id, inscription_id=self.inscription.id, ecole_id=self.ecole.id),
        ])
        db.session.commit()

        data_s1, _ = calculer_bulletin_data(self.ecole.id, self.annee, self.inscription, periode=SEMESTRE_1)
        data_s2, _ = calculer_bulletin_data(self.ecole.id, self.annee, self.inscription, periode=SEMESTRE_2)
        self.assertEqual(data_s1["nb_absences"], 1)
        self.assertEqual(data_s2["nb_absences"], 1)

    def test_06_get_configuration_ne_modifie_pas_annee_consultee(self):
        self._login()
        before = None
        with self.client.session_transaction() as sess:
            before = dict(sess.get("annee_consultee", {}))
        response = self.client.get("/annees", follow_redirects=False)
        self.assertIn(response.status_code, (200, 302))
        with self.client.session_transaction() as sess:
            self.assertEqual(dict(sess.get("annee_consultee", {})), before)
        self.assertEqual(get_semestres_annee(self.ecole.id, self.annee.id), [])


if __name__ == "__main__":
    unittest.main()
