import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription
from app.services.inscriptions_annuelles import (
    auditer_backfill_inscriptions,
    backfill_inscriptions_depuis_classe_courante,
    creer_inscription_annuelle,
    get_parcours_eleve,
    modifier_inscription_annuelle,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2AInscriptionsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        self.annee_2025 = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 7, 31),
            statut="archivee", ecole_id=self.ecole_a.id
        )
        self.annee_active = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31),
            statut="active", ecole_id=self.ecole_a.id
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027", date_debut=date(2026, 9, 1), date_fin=date(2027, 7, 31),
            statut="active", ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.annee_2025, self.annee_active, self.annee_b])
        db.session.flush()

        self.classe_6a = Classe(nom="6e A", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_2025.id)
        self.classe_5a = Classe(nom="5e A", niveau="5e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_active.id)
        self.classe_5b = Classe(nom="5e B", niveau="5e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_active.id)
        self.classe_autre_ecole = Classe(nom="5e X", niveau="5e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.annee_b.id)
        db.session.add_all([self.classe_6a, self.classe_5a, self.classe_5b, self.classe_autre_ecole])
        db.session.flush()

        self.eleve = Eleve(
            nom="Moussa",
            prenom="Abdou",
            date_naissance=date(2014, 2, 3),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_5a.id,
        )
        self.eleve_b = Eleve(
            nom="Awa",
            prenom="B",
            date_naissance=date(2014, 5, 6),
            ecole_id=self.ecole_b.id,
            classe_id=self.classe_autre_ecole.id,
        )
        db.session.add_all([self.eleve, self.eleve_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_creation_inscription_active_synchronise_classe(self):
        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_5a.id
        )
        db.session.commit()

        self.assertIsNone(error)
        self.assertEqual(Eleve.query.count(), 2)
        self.assertEqual(inscription.ecole_id, self.ecole_a.id)
        self.assertEqual(inscription.annee_scolaire_id, self.annee_active.id)
        self.assertEqual(inscription.classe_id, self.classe_5a.id)
        self.assertEqual(db.session.get(Eleve, self.eleve.id).classe_id, self.classe_5a.id)

    def test_changement_classe_modifie_inscription_sans_doublon(self):
        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_5a.id
        )
        self.assertIsNone(error)
        changed, error = modifier_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_5b.id
        )
        db.session.commit()

        self.assertIsNone(error)
        self.assertEqual(changed.id, inscription.id)
        self.assertEqual(changed.classe_id, self.classe_5b.id)
        self.assertEqual(Inscription.query.filter_by(eleve_id=self.eleve.id, annee_scolaire_id=self.annee_active.id).count(), 1)
        self.assertEqual(db.session.get(Eleve, self.eleve.id).classe_id, self.classe_5b.id)

    def test_meme_eleve_plusieurs_annees_et_parcours_intact(self):
        self.annee_2025.statut = "planifiee"
        old, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_2025.id, self.classe_6a.id
        )
        self.assertIsNone(error)
        current, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_5a.id
        )
        self.annee_2025.statut = "archivee"
        db.session.commit()

        self.assertIsNone(error)
        self.assertNotEqual(old.id, current.id)
        parcours = get_parcours_eleve(self.eleve)
        self.assertEqual([p.classe_id for p in parcours], [self.classe_6a.id, self.classe_5a.id])
        self.assertEqual([p.annee_scolaire.statut for p in parcours], ["archivee", "active"])
        self.assertEqual(db.session.get(Eleve, self.eleve.id).classe_id, self.classe_5a.id)
        self.assertEqual(Eleve.query.filter_by(nom="Moussa").count(), 1)

    def test_refus_autre_ecole_autre_annee_classe_mauvaise_annee_archive(self):
        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_autre_ecole.id
        )
        self.assertIsNone(inscription)
        self.assertIn("Classe invalide", error)

        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve_b.id, self.annee_active.id, self.classe_5a.id
        )
        self.assertIsNone(inscription)
        self.assertIn("Eleve introuvable", error)

        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_b.id, self.classe_5a.id
        )
        self.assertIsNone(inscription)
        self.assertIn("Annee scolaire invalide", error)

        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_6a.id
        )
        self.assertIsNone(inscription)
        self.assertIn("n'appartient pas", error)

        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_2025.id, self.classe_6a.id
        )
        self.assertIsNone(inscription)
        self.assertIn("archivee", error)

    def test_classe_id_pas_preuve_historique_unique(self):
        self.annee_2025.statut = "planifiee"
        old, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_2025.id, self.classe_6a.id
        )
        self.assertIsNone(error)
        current, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_5a.id
        )
        self.assertIsNone(error)
        self.eleve.classe_id = self.classe_5b.id
        db.session.commit()

        parcours = get_parcours_eleve(self.eleve)
        self.assertEqual(len(parcours), 2)
        self.assertEqual({p.id for p in parcours}, {old.id, current.id})

    def test_creation_eleve_et_inscription_atomique_si_inscription_echoue(self):
        eleve = Eleve(
            nom="Rollback",
            prenom="Test",
            date_naissance=date(2014, 10, 11),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_5a.id,
        )
        db.session.add(eleve)
        db.session.flush()
        eleve_id = eleve.id

        inscription, error = creer_inscription_annuelle(
            self.ecole_a.id, eleve_id, self.annee_active.id, self.classe_autre_ecole.id
        )
        self.assertIsNone(inscription)
        self.assertIsNotNone(error)
        db.session.rollback()

        self.assertIsNone(db.session.get(Eleve, eleve_id))
        self.assertEqual(Eleve.query.filter_by(nom="Rollback").count(), 0)

    def test_backfill_ne_duplique_pas_et_signale_incoherences(self):
        existing, error = creer_inscription_annuelle(
            self.ecole_a.id, self.eleve.id, self.annee_active.id, self.classe_5a.id
        )
        self.assertIsNone(error)
        incoherent = Eleve(
            nom="Incoherent",
            prenom="X",
            date_naissance=date(2014, 8, 9),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_autre_ecole.id,
        )
        db.session.add(incoherent)
        db.session.commit()

        stats, anomalies = auditer_backfill_inscriptions()
        self.assertGreaterEqual(stats["inscription_existante"], 1)
        self.assertGreaterEqual(stats["classe_incoherente"], 1)

        created, anomalies = backfill_inscriptions_depuis_classe_courante(commit=True)
        self.assertGreaterEqual(created, 1)
        self.assertTrue(any(a["eleve_id"] == incoherent.id for a in anomalies))
        self.assertEqual(
            Inscription.query.filter_by(eleve_id=self.eleve.id, annee_scolaire_id=self.annee_active.id).count(),
            1,
        )


if __name__ == "__main__":
    unittest.main()
