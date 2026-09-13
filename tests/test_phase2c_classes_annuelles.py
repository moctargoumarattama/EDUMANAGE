import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, NiveauScolaire
from app.services.classes_annuelles import (
    get_classes_ouvertes_annee,
    preparer_structure_classes,
    set_classe_ouverte,
)
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.niveaux import creer_classe_depuis_niveau, ensure_ecole_niveau_configs, set_cycle_actif, set_niveau_actif


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase2CClassesAnnuellesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.ecole_a = Ecole(nom="Ecole A")
        self.ecole_b = Ecole(nom="Ecole B")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()
        ensure_ecole_niveau_configs(self.ecole_a.id, commit=True)
        ensure_ecole_niveau_configs(self.ecole_b.id, commit=True)

        set_cycle_actif(self.ecole_a.id, "primaire", False)
        self.sixieme = NiveauScolaire.query.filter_by(code="6E").first()
        self.cinquieme = NiveauScolaire.query.filter_by(code="5E").first()
        self.quatrieme = NiveauScolaire.query.filter_by(code="4E").first()
        self.troisieme = NiveauScolaire.query.filter_by(code="3E").first()
        self.cp = NiveauScolaire.query.filter_by(code="CP").first()

        self.annee_source = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_cible = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_source, self.annee_cible, self.annee_b])
        db.session.commit()

        from app.services.structure_annuelle import sauvegarder_structure_annee
        college_nids = [self.sixieme.id, self.cinquieme.id, self.quatrieme.id, self.troisieme.id]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_source.id, college_nids)
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_cible.id, college_nids)
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b.id, college_nids)
        db.session.commit()

        for niveau, nom, section in [
            (self.sixieme, "6e A", "A"),
            (self.sixieme, "6e B", "B"),
            (self.cinquieme, "5e A", "A"),
            (self.quatrieme, "4e A", "A"),
            (self.troisieme, "3e A", "A"),
        ]:
            classe, error = creer_classe_depuis_niveau(
                self.ecole_a.id,
                self.annee_source.id,
                niveau.id,
                nom=nom,
                section=section,
            )
            self.assertIsNone(error)

        self.classe_6a = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_source.id, nom="6e A").first()
        self.classe_6b = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_source.id, nom="6e B").first()
        classe_b, error = creer_classe_depuis_niveau(
            self.ecole_b.id,
            self.annee_b.id,
            self.sixieme.id,
            nom="6e X",
            section="X",
        )
        self.assertIsNone(error)
        self.classe_b = classe_b

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_preparer_structure_copie_classes_sans_eleves_et_sans_doublons(self):
        result, error = preparer_structure_classes(
            self.ecole_a.id,
            self.annee_cible.id,
            self.annee_source.id,
            statuts={self.classe_6b.id: "fermee"},
        )
        self.assertIsNone(error)
        self.assertEqual(len(result["created"]), 5)
        self.assertEqual(Inscription.query.filter_by(annee_scolaire_id=self.annee_cible.id).count(), 0)

        copied = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_cible.id).all()
        self.assertEqual(len(copied), 5)
        self.assertTrue(all(c.id not in {self.classe_6a.id, self.classe_6b.id} for c in copied))
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_source.id).count(), 5)
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_b.id).count(), 1)
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_cible.id, nom="6e B").first().statut, "fermee")

        second, error = preparer_structure_classes(self.ecole_a.id, self.annee_cible.id, self.annee_source.id)
        self.assertIsNone(error)
        self.assertEqual(len(second["created"]), 0)
        self.assertEqual(Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_cible.id).count(), 5)

    def test_classe_fermee_non_utilisable_pour_nouvelle_inscription(self):
        preparer_structure_classes(
            self.ecole_a.id,
            self.annee_cible.id,
            self.annee_source.id,
            statuts={self.classe_6b.id: "fermee"},
        )
        classe_ouverte = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_cible.id, nom="6e A").first()
        classe_fermee = Classe.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_cible.id, nom="6e B").first()
        eleve = Eleve(nom="Moussa", prenom="A", date_naissance=date(2014, 1, 1), ecole_id=self.ecole_a.id, classe_id=classe_ouverte.id)
        db.session.add(eleve)
        db.session.flush()

        inscription, error = creer_inscription_annuelle(self.ecole_a.id, eleve.id, self.annee_cible.id, classe_fermee.id)
        self.assertIsNone(inscription)
        self.assertIn("fermee", error)

        inscription, error = creer_inscription_annuelle(self.ecole_a.id, eleve.id, self.annee_cible.id, classe_ouverte.id)
        self.assertIsNone(error)
        self.assertEqual(inscription.classe_id, classe_ouverte.id)
        self.assertEqual([c.nom for c in get_classes_ouvertes_annee(self.ecole_a.id, self.annee_cible.id).order_by(Classe.nom).all()], ["3e A", "4e A", "5e A", "6e A"])

    def test_archive_et_multi_ecoles_proteges(self):
        self.annee_cible.statut = "archivee"
        db.session.commit()
        result, error = preparer_structure_classes(self.ecole_a.id, self.annee_cible.id, self.annee_source.id)
        self.assertIsNone(result)
        self.assertIn("archivee", error)

        classe, error = set_classe_ouverte(self.ecole_a.id, self.classe_b.id, False)
        self.assertIsNone(classe)
        self.assertIn("introuvable", error)

    def test_niveaux_desactives_non_copies(self):
        from app.models import AnneeNiveauConfig
        cfg = AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_cible.id, niveau_id=self.sixieme.id).first()
        if cfg:
            cfg.actif = False
            db.session.commit()
        result, error = preparer_structure_classes(self.ecole_a.id, self.annee_cible.id, self.annee_source.id)
        self.assertIsNone(error)
        self.assertEqual({c.nom for c in result["created"]}, {"5e A", "4e A", "3e A"})
        self.assertEqual({c.nom for c in result["skipped"]}, {"6e A", "6e B"})

    def test_nouvelle_ecole_sans_historique_ne_cree_pas_de_classe_arbitraire(self):
        ecole_c = Ecole(nom="Ecole C")
        db.session.add(ecole_c)
        db.session.flush()
        ensure_ecole_niveau_configs(ecole_c.id, commit=False)
        annee_c = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="planifiee",
            ecole_id=ecole_c.id,
        )
        db.session.add(annee_c)
        db.session.commit()

        result, error = preparer_structure_classes(ecole_c.id, annee_c.id)
        self.assertIsNone(error)
        self.assertEqual(result["created"], [])
        self.assertEqual(Classe.query.filter_by(ecole_id=ecole_c.id).count(), 0)


if __name__ == "__main__":
    unittest.main()
