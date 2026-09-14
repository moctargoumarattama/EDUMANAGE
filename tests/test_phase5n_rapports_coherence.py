import unittest
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app, db
from app.models import Absence, AnneeScolaire, Classe, Cours, Ecole, Eleve, Inscription, Note, Paiement, Utilisateur
from app.services.absences_annuelles import get_absences_annee
from app.services.bulletins_annuels import calculer_bulletin_data
from app.services.notes_annuelles import SEMESTRE_1, TYPE_COMPOSITION, TYPE_DEVOIR
from app.services.paiements_annuels import get_finances_inscription
from app.services.statistiques_annuelles import get_rapports_annuels
from app.routes.rapports import _rapport_export_rows


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-phase5n"


class Phase5NRapportsCoherenceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole = Ecole(nom="Ecole A", statut="actif")
        self.ecole_b = Ecole(nom="Ecole B", statut="actif")
        self.ecole.setup_step = "complete"
        self.ecole.setup_complete = True
        self.ecole_b.setup_step = "complete"
        self.ecole_b.setup_complete = True
        db.session.add_all([self.ecole, self.ecole_b])
        db.session.flush()

        self.admin = Utilisateur(nom="Admin", email="phase5n@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole.id)
        self.annee = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 6, 30), statut="active", ecole_id=self.ecole.id)
        self.annee_old = AnneeScolaire(nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30), statut="archivee", ecole_id=self.ecole.id)
        self.annee_b = AnneeScolaire(nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 6, 30), statut="active", ecole_id=self.ecole_b.id)
        db.session.add_all([self.admin, self.annee, self.annee_old, self.annee_b])
        db.session.flush()

        self.classe = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        self.classe_old = Classe(nom="6e Old", niveau="6e", ecole_id=self.ecole.id, annee_scolaire_id=self.annee_old.id)
        self.classe_b = Classe(nom="6e B", niveau="6e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe, self.classe_old, self.classe_b])
        db.session.flush()

        self.eleve1 = Eleve(nom="A", prenom="Un", date_naissance=date(2014, 1, 1), genre="M", ecole_id=self.ecole.id)
        self.eleve2 = Eleve(nom="B", prenom="Deux", date_naissance=date(2014, 1, 2), genre="F", ecole_id=self.ecole.id)
        self.eleve_old = Eleve(nom="Old", prenom="O", date_naissance=date(2013, 1, 1), ecole_id=self.ecole.id)
        self.eleve_b = Eleve(nom="Other", prenom="B", date_naissance=date(2014, 1, 3), ecole_id=self.ecole_b.id)
        db.session.add_all([self.eleve1, self.eleve2, self.eleve_old, self.eleve_b])
        db.session.flush()

        self.ins1 = Inscription(eleve_id=self.eleve1.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id, frais_annuels=1000)
        self.ins2 = Inscription(eleve_id=self.eleve2.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id, ecole_id=self.ecole.id, frais_annuels=1000)
        self.ins_old = Inscription(eleve_id=self.eleve_old.id, classe_id=self.classe_old.id, annee_scolaire_id=self.annee_old.id, ecole_id=self.ecole.id, frais_annuels=1000)
        self.ins_b = Inscription(eleve_id=self.eleve_b.id, classe_id=self.classe_b.id, annee_scolaire_id=self.annee_b.id, ecole_id=self.ecole_b.id, frais_annuels=1000)
        db.session.add_all([self.ins1, self.ins2, self.ins_old, self.ins_b])
        db.session.flush()

        self.cours = Cours(nom="Math", coefficient=2, ecole_id=self.ecole.id, classe_id=self.classe.id)
        self.cours_b = Cours(nom="Math B", coefficient=2, ecole_id=self.ecole_b.id, classe_id=self.classe_b.id)
        db.session.add_all([self.cours, self.cours_b])
        db.session.flush()

        db.session.add_all([
            Note(valeur=12, type_evaluation=TYPE_DEVOIR, periode=SEMESTRE_1, eleve_id=self.eleve1.id, cours_id=self.cours.id, ecole_id=self.ecole.id, inscription_id=self.ins1.id, annee_id=self.annee.id),
            Note(valeur=16, type_evaluation=TYPE_COMPOSITION, periode=SEMESTRE_1, eleve_id=self.eleve1.id, cours_id=self.cours.id, ecole_id=self.ecole.id, inscription_id=self.ins1.id, annee_id=self.annee.id),
            Absence(date_absence=date(2027, 1, 10), eleve_id=self.eleve1.id, inscription_id=self.ins1.id, ecole_id=self.ecole.id),
            Absence(date_absence=date(2025, 1, 10), eleve_id=self.eleve_old.id, inscription_id=self.ins_old.id, ecole_id=self.ecole.id),
            Paiement(montant=400, mois="Septembre", annee=2026, eleve_id=self.eleve1.id, inscription_id=self.ins1.id, ecole_id=self.ecole.id, date_paiement=datetime(2026, 9, 10)),
            Paiement(montant=900, mois="Septembre", annee=2026, eleve_id=self.eleve_b.id, inscription_id=self.ins_b.id, ecole_id=self.ecole_b.id, date_paiement=datetime(2026, 9, 10)),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_01_effectif_inscriptions_annee_consultee(self):
        rapports = get_rapports_annuels(self.ecole.id, self.annee)
        self.assertEqual(rapports["statistiques"]["total_eleves"], 2)
        self.assertEqual(rapports["classes_data"][0]["effectif"], 2)

    def test_02_moyenne_rapport_identique_bulletin(self):
        attendu, err = calculer_bulletin_data(self.ecole.id, self.annee, self.ins1, periode=SEMESTRE_1)
        self.assertIsNone(err)
        rapports = get_rapports_annuels(self.ecole.id, self.annee)
        self.assertEqual(rapports["classes_data"][0]["meilleur_eleve"]["moyenne"], attendu["moyenne_generale"])
        self.assertEqual(rapports["statistiques"]["moyenne_generale"], attendu["moyenne_generale"])

    def test_03_absences_rapport_identiques_service_absences(self):
        rapports = get_rapports_annuels(self.ecole.id, self.annee)
        absences = get_absences_annee(self.ecole.id, self.annee, self.admin)
        self.assertEqual(rapports["statistiques"]["total_absences"], len(absences))

    def test_04_paiements_restes_identiques_service_et_zero_paiement_inclus(self):
        rapports = get_rapports_annuels(self.ecole.id, self.annee)
        finances1 = get_finances_inscription(self.ins1)
        finances2 = get_finances_inscription(self.ins2)
        self.assertEqual(rapports["statistiques"]["frais_attendus"], finances1["frais_annuels"] + finances2["frais_annuels"])
        self.assertEqual(rapports["statistiques"]["total_encaisse"], finances1["total_paye"] + finances2["total_paye"])
        self.assertEqual(rapports["statistiques"]["reste_a_payer"], finances1["reste_a_payer"] + finances2["reste_a_payer"])
        self.assertEqual(rapports["statistiques"]["eleves_non_payes"], 1)

    def test_05_annee_differente_stats_differentes_sans_session(self):
        rapports_actuelle = get_rapports_annuels(self.ecole.id, self.annee)
        rapports_old = get_rapports_annuels(self.ecole.id, self.annee_old)
        self.assertNotEqual(rapports_actuelle["statistiques"]["total_eleves"], rapports_old["statistiques"]["total_eleves"])

    def test_06_ecole_a_sans_donnee_ecole_b(self):
        rapports = get_rapports_annuels(self.ecole.id, self.annee)
        self.assertEqual(rapports["statistiques"]["total_encaisse"], 400)
        self.assertEqual(rapports["statistiques"]["total_eleves"], 2)
        export_rows = _rapport_export_rows(rapports)
        self.assertEqual(export_rows[0]["Effectif"], rapports["classes_data"][0]["effectif"])
        self.assertEqual(export_rows[0]["Total encaissé"], rapports["classes_data"][0]["finances"]["total_encaisse"])
        self.assertEqual(export_rows[0]["Reste à payer"], rapports["classes_data"][0]["finances"]["reste_a_payer"])


if __name__ == "__main__":
    unittest.main()
