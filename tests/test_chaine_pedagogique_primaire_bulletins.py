import unittest
from datetime import date

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import AnneeScolaire, Classe, Cours, Ecole, Eleve, Inscription, Note, PeriodeBulletin, Utilisateur
from app.services.bulletins_annuels import verifier_publication_periode
from app.services.evaluations import calculer_completude_inscription
from app.services.semestres import (
    calendrier_configure,
    calendrier_configure_compositions,
    configurer_compositions_annee,
    configurer_semestres_annee,
)
from app.services.dispenses import accorder_dispense


class _Config:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    SECRET_KEY = "chaine-pedagogique-tests"


class ChainePedagogiquePrimaireBulletinsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_Config)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.ecole = Ecole(nom="Ecole test", statut="actif", onboarding_complete=True)
        db.session.add(self.ecole)
        db.session.flush()
        self.annee = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30),
            statut="active", ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.flush()
        self.admin = Utilisateur(
            nom="Admin", prenom="Test", email="admin-chaine@example.test",
            mot_de_passe=generate_password_hash("Secret123!"), role="admin",
            ecole_id=self.ecole.id, statut="actif",
        )
        self.classe = Classe(nom="CM2 A", niveau="CM2", ecole_id=self.ecole.id, annee_scolaire_id=self.annee.id)
        db.session.add_all([self.admin, self.classe])
        db.session.flush()
        self.cours = Cours(nom="Mathématiques", coefficient=1, ecole_id=self.ecole.id, classe_id=self.classe.id)
        self.eleve = Eleve(nom="Test", prenom="Awa", date_naissance=date(2014, 1, 1), ecole_id=self.ecole.id)
        db.session.add_all([self.cours, self.eleve])
        db.session.flush()
        self.inscription = Inscription(
            eleve_id=self.eleve.id, classe_id=self.classe.id, annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id, statut="inscrit",
        )
        db.session.add(self.inscription)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _note(self, valeur, type_evaluation, periode="1ère Composition"):
        note = Note(
            valeur=valeur, coefficient=1, type_evaluation=type_evaluation, periode=periode,
            inscription_id=self.inscription.id, eleve_id=self.eleve.id, cours_id=self.cours.id,
            ecole_id=self.ecole.id, annee_id=self.annee.id,
        )
        db.session.add(note)
        db.session.commit()

    def test_primaire_configure_trois_compositions_datees(self):
        periodes, erreur = configurer_compositions_annee(
            self.ecole.id, self.annee.id, [date(2025, 11, 30), date(2026, 2, 28), date(2026, 5, 31)]
        )
        self.assertIsNone(erreur)
        self.assertEqual([p.nom for p in periodes], ["1ère Composition", "2ème Composition", "3ème Composition"])
        self.assertTrue(calendrier_configure_compositions(self.ecole.id, self.annee.id))

    def test_devoir_seul_ne_permet_pas_la_completude(self):
        configurer_compositions_annee(self.ecole.id, self.annee.id, [date(2025, 11, 30), date(2026, 2, 28), date(2026, 5, 31)])
        self._note(12, "Devoir")
        avis = calculer_completude_inscription(self.ecole.id, self.annee.id, self.inscription, "1ère Composition", True)
        self.assertFalse(avis["is_pedagogically_complete"])
        self.assertIn("Mathématiques", avis["missing_composition_subjects_names"])

    def test_devoir_et_composition_autorisent_la_completude(self):
        self._note(12, "Devoir")
        self._note(14, "Composition")
        avis = calculer_completude_inscription(self.ecole.id, self.annee.id, self.inscription, "1ère Composition", True)
        self.assertTrue(avis["is_pedagogically_complete"])
        self.assertEqual(avis["status"], "complete")

    def test_dispense_medicale_validee_ne_bloque_pas(self):
        dispense, erreur = accorder_dispense(
            self.ecole.id, self.inscription.id, self.cours.id, "1ère Composition", "CERT-001", self.admin.id
        )
        self.assertIsNone(erreur)
        self.assertIsNotNone(dispense)
        avis = calculer_completude_inscription(self.ecole.id, self.annee.id, self.inscription, "1ère Composition", True)
        self.assertTrue(avis["is_pedagogically_complete"])
        self.assertEqual(avis["status"], "complete")
        self.assertIn("Mathématiques", avis["dispensed_subjects_names"])

    def test_publication_refusee_sans_composition_puis_autorisee(self):
        configurer_compositions_annee(self.ecole.id, self.annee.id, [date(2025, 11, 30), date(2026, 2, 28), date(2026, 5, 31)])
        periode = PeriodeBulletin.query.filter_by(annee_id=self.annee.id, nom="1ère Composition").first()
        periode.periode_active = True
        self._note(12, "Devoir")
        ok, _ = verifier_publication_periode(periode)
        self.assertFalse(ok)
        self._note(14, "Composition")
        ok, erreur = verifier_publication_periode(periode)
        self.assertTrue(ok, erreur)

    def test_secondaire_conserve_deux_semestres(self):
        result, erreur = configurer_semestres_annee(self.ecole.id, self.annee.id, date(2026, 1, 31))
        self.assertIsNone(erreur)
        self.assertEqual(len(result), 2)
        self.assertTrue(calendrier_configure(self.ecole.id, self.annee.id))


if __name__ == "__main__":
    unittest.main()
