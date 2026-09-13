import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, NiveauScolaire, Utilisateur
from app.services.niveaux import (
    STANDARD_NIVEAUX,
    creer_classe_depuis_niveau,
    ensure_ecole_niveau_configs,
    ensure_standard_niveaux,
    get_niveaux_actifs,
    modifier_classe_depuis_niveau,
    set_cycle_actif,
    set_niveau_actif,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase1NiveauxTestCase(unittest.TestCase):
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
        self.annee_a = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.annee_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.annee_a, self.annee_b])
        db.session.commit()
        from app.services.structure_annuelle import sauvegarder_structure_annee
        all_nids = [n.id for n in NiveauScolaire.query.all()]
        sauvegarder_structure_annee(self.ecole_a.id, self.annee_a.id, all_nids)
        sauvegarder_structure_annee(self.ecole_b.id, self.annee_b.id, all_nids)
        db.session.commit()
        self.admin = Utilisateur(nom="Admin", email="admin@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id)
        self.super_admin = Utilisateur(nom="Super", email="super@test.local", mot_de_passe="x", role="super_admin")
        self.professeur = Utilisateur(nom="Prof", email="prof@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id)
        self.parent = Utilisateur(nom="Parent", email="parent@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id)
        db.session.add_all([self.admin, self.super_admin, self.professeur, self.parent])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def niveau(self, code):
        return NiveauScolaire.query.filter_by(code=code).first()

    def test_catalogue_standard_et_ordre(self):
        niveaux = ensure_standard_niveaux(commit=True)
        self.assertEqual([n.code for n in niveaux], [code for code, _nom, _cycle in STANDARD_NIVEAUX])
        self.assertEqual(niveaux[0].code, "CI")
        self.assertEqual(niveaux[-1].code, "TERMINALE")
        self.assertIsNone(niveaux[-1].niveau_suivant_id)
        self.assertEqual(niveaux[0].niveau_suivant.code, "CP")

    def test_configs_independantes_par_ecole(self):
        sixieme = self.niveau("6E")
        set_niveau_actif(self.ecole_a.id, sixieme.id, False)
        actifs_a = {n.code for n in get_niveaux_actifs(self.ecole_a.id)}
        actifs_b = {n.code for n in get_niveaux_actifs(self.ecole_b.id)}
        self.assertNotIn("6E", actifs_a)
        self.assertIn("6E", actifs_b)

    def test_desactiver_cycles_ne_supprime_pas_les_classes(self):
        sixieme = self.niveau("6E")
        classe, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(error)
        set_cycle_actif(self.ecole_a.id, "secondaire", False)
        self.assertIsNotNone(db.session.get(Classe, classe.id))

    def test_creation_classe_niveau_actif_ok_et_sections_strictes(self):
        sixieme = self.niveau("6E")
        for section in ["A", "b"]:
            classe, error = creer_classe_depuis_niveau(
                self.ecole_a.id, self.annee_a.id, sixieme.id, nom="Nom falsifie", section=section
            )
            self.assertIsNone(error)
            self.assertEqual(classe.section, section.upper())
            self.assertEqual(classe.nom, f"6e {section.upper()}")

        classe, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e 1", section="1"
        )
        self.assertIsNone(classe)
        self.assertIn("une seule lettre", error)

        terminale = self.niveau("TERMINALE")
        classe, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, terminale.id, nom="Terminale D1", section="D"
        )
        self.assertIsNone(error)
        self.assertEqual(classe.nom, "Terminale Serie D")

    def test_creation_classe_niveau_desactive_refusee(self):
        sixieme = self.niveau("6E")
        from app.models import AnneeNiveauConfig
        cfg = AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id, niveau_id=sixieme.id).first()
        if cfg:
            cfg.actif = False
            db.session.commit()
        classe, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(classe)
        self.assertIn("pas retenu", error)

    def test_meme_nom_autorise_sur_deux_annees_et_doublon_refuse(self):
        sixieme = self.niveau("6E")
        classe, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(error)
        doublon, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(doublon)
        self.assertIn("existe deja", error)

        annee_suivante = AnneeScolaire(
            nom="2027-2028",
            date_debut=date(2027, 9, 1),
            date_fin=date(2028, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        db.session.add(annee_suivante)
        db.session.commit()
        from app.services.structure_annuelle import sauvegarder_structure_annee
        sauvegarder_structure_annee(self.ecole_a.id, annee_suivante.id, [sixieme.id])
        db.session.commit()
        autre, error = creer_classe_depuis_niveau(
            self.ecole_a.id, annee_suivante.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(error)
        self.assertEqual(autre.annee_scolaire_id, annee_suivante.id)

    def test_annee_archivee_refuse_nouvelle_classe(self):
        self.annee_a.statut = "archivee"
        db.session.commit()
        classe, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, self.niveau("6E").id, nom="6e A"
        )
        self.assertIsNone(classe)
        self.assertIn("archivee", error)

    def test_modifier_classe_controles_phase1(self):
        sixieme = self.niveau("6E")
        cinquieme = self.niveau("5E")
        classe, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(error)

        updated, error = modifier_classe_depuis_niveau(
            classe, self.ecole_a.id, cinquieme.id, nom="5e A", section="A", capacite=40
        )
        self.assertIsNone(error)
        self.assertEqual(updated.niveau_id, cinquieme.id)
        self.assertEqual(updated.capacite, 40)

        from app.models import AnneeNiveauConfig
        cfg = AnneeNiveauConfig.query.filter_by(ecole_id=self.ecole_a.id, annee_scolaire_id=self.annee_a.id, niveau_id=sixieme.id).first()
        if cfg:
            cfg.actif = False
            db.session.commit()
        updated, error = modifier_classe_depuis_niveau(
            classe, self.ecole_a.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(updated)
        self.assertTrue("desactive" in error or "pas retenu" in error)

    def test_modifier_classe_refuse_archive_doublon_et_autre_ecole(self):
        sixieme = self.niveau("6E")
        classe_a, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e A", section="A"
        )
        self.assertIsNone(error)
        _classe_b, error = creer_classe_depuis_niveau(
            self.ecole_a.id, self.annee_a.id, sixieme.id, nom="6e B", section="B"
        )
        self.assertIsNone(error)

        updated, error = modifier_classe_depuis_niveau(
            classe_a, self.ecole_b.id, sixieme.id, nom="6e C", section="C"
        )
        self.assertIsNone(updated)
        self.assertIn("introuvable", error)

        updated, error = modifier_classe_depuis_niveau(
            classe_a, self.ecole_a.id, sixieme.id, nom="6e B", section="B"
        )
        self.assertIsNone(updated)
        self.assertIn("existe deja", error)

        self.annee_a.statut = "archivee"
        db.session.commit()
        updated, error = modifier_classe_depuis_niveau(
            classe_a, self.ecole_a.id, sixieme.id, nom="6e C", section="C"
        )
        self.assertIsNone(updated)
        self.assertIn("archivee", error)

    def test_parametres_pedagogiques_reserve_admin(self):
        creer_classe_depuis_niveau(
            self.ecole_a.id,
            self.annee_a.id,
            self.niveau("6E").id,
            nom="6e A",
            section="A",
        )
        client = self.app.test_client()

        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        self.assertEqual(client.get("/parametres-pedagogiques").status_code, 200)

        with client.session_transaction() as session:
            session["_user_id"] = str(self.super_admin.id)
            session["_fresh"] = True
            session["ecole_id"] = self.ecole_a.id
        self.assertEqual(client.get("/parametres-pedagogiques").status_code, 200)

        with client.session_transaction() as session:
            session["_user_id"] = str(self.professeur.id)
            session["_fresh"] = True
        self.assertEqual(client.get("/parametres-pedagogiques").status_code, 302)

        with client.session_transaction() as session:
            session["_user_id"] = str(self.parent.id)
            session["_fresh"] = True
        self.assertEqual(client.get("/parametres-pedagogiques").status_code, 302)


if __name__ == "__main__":
    unittest.main()
