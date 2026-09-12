"""
tests/test_phase2d2_passage_annee.py
=============================================================
KLASORA — Phase 2D-2
Tests du service de passage d'année individuel.

Couverture :
  - Contexte source/cible (validation)
  - Promotion (passage)
  - Redoublement
  - Transfert
  - Sortie
  - Diplôme
  - fin_cycle
  - Terminale → passage refusé
  - Niveau cible incorrect (anti-forgerie)
  - Classe cible fermée
  - Niveau cible non retenu (AnneeNiveauConfig)
  - Classe source sans niveau_id (legacy)
  - Idempotence / conflit
  - Atomicité (rollback)
  - Cible active → sync Eleve.classe_id
  - Cible planifiée → Eleve.classe_id inchangé
  - Source archivée → refus de mutation
  - Multi-écoles → refus systématique
  - preparer_passage_eleve → aucune mutation
=============================================================
"""

import unittest
from datetime import date

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    AnneeNiveauConfig,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
)
from app.services.niveaux import ensure_standard_niveaux
from app.services.niveaux_annuels import sauvegarder_selection_annuelle
from app.services.inscriptions_annuelles import creer_inscription_annuelle
from app.services.passage_annee import (
    executer_passage_eleve,
    get_classes_candidates_passage,
    preparer_passage_eleve,
    valider_contexte_passage,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _creer_ecole(nom):
    e = Ecole(nom=nom)
    db.session.add(e)
    db.session.flush()
    return e


def _creer_annee(ecole_id, nom, debut, fin, statut):
    a = AnneeScolaire(
        nom=nom,
        date_debut=debut,
        date_fin=fin,
        statut=statut,
        ecole_id=ecole_id,
    )
    db.session.add(a)
    db.session.flush()
    return a


def _creer_classe(ecole_id, annee_id, nom, niveau_id=None, statut="ouverte"):
    c = Classe(
        nom=nom,
        niveau=nom,
        niveau_id=niveau_id,
        ecole_id=ecole_id,
        annee_scolaire_id=annee_id,
        statut=statut,
    )
    db.session.add(c)
    db.session.flush()
    return c


def _creer_eleve(ecole_id, classe_id, nom="Eleve", prenom="Test"):
    e = Eleve(
        nom=nom,
        prenom=prenom,
        date_naissance=date(2010, 1, 1),
        ecole_id=ecole_id,
        classe_id=classe_id,
    )
    db.session.add(e)
    db.session.flush()
    return e


def _inscrire(ecole_id, eleve_id, annee_id, classe_id, statut="inscrit"):
    insc, err = creer_inscription_annuelle(
        ecole_id, eleve_id, annee_id, classe_id, statut=statut, sync_active=False
    )
    assert err is None, f"Erreur création inscription : {err}"
    db.session.flush()
    return insc


def _activer_niveaux_pour_annee(ecole_id, annee_id, niveaux):
    """Active les niveaux donnés dans AnneeNiveauConfig pour l'année."""
    ids = [n.id for n in niveaux]
    _, err = sauvegarder_selection_annuelle(ecole_id, annee_id, ids)
    assert err is None, f"Erreur AnneeNiveauConfig : {err}"
    db.session.flush()


# ---------------------------------------------------------------------------
# Fixture de base réutilisable
# ---------------------------------------------------------------------------

class BasePassageTestCase(unittest.TestCase):
    """
    Construit un contexte de test minimal :

    École A :
      2025-2026  active
        6e A     (niveau 6E, ouverte)
      2026-2027  planifiée
        5e A     (niveau 5E, ouverte)
        5e B     (niveau 5E, ouverte)
        6e B     (niveau 6E, ouverte)
        Term A   (niveau TERMINALE, ouverte)

    École B :
      2025-2026  active
        6e B     (quelconque)

    Niveaux standard : ensure_standard_niveaux()
    """

    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Niveaux standard
        niveaux = ensure_standard_niveaux(commit=False)
        db.session.flush()
        # Index par code
        self.niv = {n.code: n for n in niveaux}

        # Écoles
        self.ecole_a = _creer_ecole("Ecole A")
        self.ecole_b = _creer_ecole("Ecole B")

        # Années école A
        self.annee_src = _creer_annee(
            self.ecole_a.id, "2025-2026",
            date(2025, 9, 1), date(2026, 7, 31), "active"
        )
        self.annee_cible = _creer_annee(
            self.ecole_a.id, "2026-2027",
            date(2026, 9, 1), date(2027, 7, 31), "planifiee"
        )

        # Année école B
        self.annee_b = _creer_annee(
            self.ecole_b.id, "2025-2026",
            date(2025, 9, 1), date(2026, 7, 31), "active"
        )

        # Classes école A, année source
        self.cls_6a = _creer_classe(
            self.ecole_a.id, self.annee_src.id, "6e A",
            niveau_id=self.niv["6E"].id
        )
        # Classes école A, année cible
        self.cls_5a = _creer_classe(
            self.ecole_a.id, self.annee_cible.id, "5e A",
            niveau_id=self.niv["5E"].id
        )
        self.cls_5b = _creer_classe(
            self.ecole_a.id, self.annee_cible.id, "5e B",
            niveau_id=self.niv["5E"].id
        )
        self.cls_6b_cible = _creer_classe(
            self.ecole_a.id, self.annee_cible.id, "6e B",
            niveau_id=self.niv["6E"].id
        )
        self.cls_term_a = _creer_classe(
            self.ecole_a.id, self.annee_cible.id, "Terminale A",
            niveau_id=self.niv["TERMINALE"].id
        )

        # Classe école A source Terminale (pour test diplôme)
        self.cls_term_src = _creer_classe(
            self.ecole_a.id, self.annee_src.id, "Terminale Src",
            niveau_id=self.niv["TERMINALE"].id
        )

        # Classe école B
        self.cls_b = _creer_classe(
            self.ecole_b.id, self.annee_b.id, "6e B_B",
            niveau_id=self.niv["6E"].id
        )

        # Activer niveaux dans AnneeNiveauConfig pour l'année cible
        _activer_niveaux_pour_annee(
            self.ecole_a.id, self.annee_cible.id,
            [self.niv["5E"], self.niv["6E"], self.niv["TERMINALE"]]
        )

        # Élève principal (Moussa) → inscrit dans classe 6e A source
        self.moussa = _creer_eleve(
            self.ecole_a.id, self.cls_6a.id, "Moussa", "Test"
        )
        self.insc_src = _inscrire(
            self.ecole_a.id, self.moussa.id, self.annee_src.id, self.cls_6a.id
        )

        # Élève Terminale → pour test diplôme
        self.eleve_term = _creer_eleve(
            self.ecole_a.id, self.cls_term_src.id, "Diallo", "Term"
        )
        self.insc_term_src = _inscrire(
            self.ecole_a.id, self.eleve_term.id, self.annee_src.id, self.cls_term_src.id
        )

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()


# ===========================================================================
# Tests : valider_contexte_passage
# ===========================================================================

class TestValiderContexte(BasePassageTestCase):

    def test_contexte_valide(self):
        src, cible, err = valider_contexte_passage(
            self.ecole_a.id, self.annee_src.id, self.annee_cible.id
        )
        self.assertIsNone(err)
        self.assertEqual(src.id, self.annee_src.id)
        self.assertEqual(cible.id, self.annee_cible.id)

    def test_annee_source_inconnue(self):
        _, _, err = valider_contexte_passage(self.ecole_a.id, 9999, self.annee_cible.id)
        self.assertIsNotNone(err)

    def test_annee_cible_inconnue(self):
        _, _, err = valider_contexte_passage(self.ecole_a.id, self.annee_src.id, 9999)
        self.assertIsNotNone(err)

    def test_annee_autre_ecole_refusee(self):
        # École B ne peut pas utiliser les années de l'école A
        _, _, err = valider_contexte_passage(
            self.ecole_b.id, self.annee_src.id, self.annee_cible.id
        )
        self.assertIsNotNone(err)

    def test_source_egale_cible(self):
        _, _, err = valider_contexte_passage(
            self.ecole_a.id, self.annee_src.id, self.annee_src.id
        )
        self.assertIsNotNone(err)

    def test_ordre_chronologique_inverse_refuse(self):
        # Mettre cible avant source chronologiquement
        _, _, err = valider_contexte_passage(
            self.ecole_a.id, self.annee_cible.id, self.annee_src.id
        )
        self.assertIsNotNone(err)

    def test_cible_archivee_refusee(self):
        self.annee_cible.statut = "archivee"
        db.session.flush()
        _, _, err = valider_contexte_passage(
            self.ecole_a.id, self.annee_src.id, self.annee_cible.id
        )
        self.assertIsNotNone(err)


# ===========================================================================
# Tests : get_classes_candidates_passage
# ===========================================================================

class TestClassesCandidates(BasePassageTestCase):

    def test_candidates_passage_niveau_5e(self):
        candidates = get_classes_candidates_passage(
            self.ecole_a.id, self.annee_cible.id, self.niv["5E"].id
        )
        ids = [c.id for c in candidates]
        self.assertIn(self.cls_5a.id, ids)
        self.assertIn(self.cls_5b.id, ids)
        self.assertNotIn(self.cls_6a.id, ids)

    def test_candidates_vides_si_niveau_desactive(self):
        # Désactiver 5E dans l'année cible
        config = AnneeNiveauConfig.query.filter_by(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niv["5E"].id,
        ).first()
        if config:
            config.actif = False
            db.session.flush()

        candidates = get_classes_candidates_passage(
            self.ecole_a.id, self.annee_cible.id, self.niv["5E"].id
        )
        self.assertEqual(candidates, [])

    def test_candidates_exclut_classes_fermees(self):
        self.cls_5a.statut = "fermee"
        db.session.flush()
        candidates = get_classes_candidates_passage(
            self.ecole_a.id, self.annee_cible.id, self.niv["5E"].id
        )
        ids = [c.id for c in candidates]
        self.assertNotIn(self.cls_5a.id, ids)
        self.assertIn(self.cls_5b.id, ids)


# ===========================================================================
# Tests : preparer_passage_eleve (LECTURE SEULE)
# ===========================================================================

class TestPreparerPassage(BasePassageTestCase):

    def test_preparer_ne_mute_pas_la_base(self):
        count_avant = Inscription.query.count()
        preparer_passage_eleve(
            self.ecole_a.id,
            self.moussa.id,
            self.annee_src.id,
            self.annee_cible.id,
            "passage",
        )
        db.session.flush()
        self.assertEqual(Inscription.query.count(), count_avant,
                         "preparer_passage_eleve ne doit pas créer d'inscription")

    def test_preparer_retourne_niveau_cible_et_candidates(self):
        result = preparer_passage_eleve(
            self.ecole_a.id,
            self.moussa.id,
            self.annee_src.id,
            self.annee_cible.id,
            "passage",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["niveau_cible"].code, "5E")
        self.assertGreaterEqual(len(result["classes_candidates"]), 1)
        self.assertFalse(result["deja_inscrit_cible"])

    def test_preparer_signale_blocage_eleve_inconnu(self):
        result = preparer_passage_eleve(
            self.ecole_a.id, 9999,
            self.annee_src.id, self.annee_cible.id, "passage"
        )
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["blocage"])

    def test_preparer_detecte_deja_inscrit(self):
        _inscrire(
            self.ecole_a.id, self.moussa.id,
            self.annee_cible.id, self.cls_5a.id
        )
        db.session.commit()
        result = preparer_passage_eleve(
            self.ecole_a.id, self.moussa.id,
            self.annee_src.id, self.annee_cible.id, "passage"
        )
        self.assertTrue(result["deja_inscrit_cible"])


# ===========================================================================
# Tests : executer_passage_eleve — PROMOTION
# ===========================================================================

class TestPassagePromotion(BasePassageTestCase):

    def _promouvoir_5b(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        db.session.commit()
        return result, err

    def test_promotion_ok(self):
        result, err = self._promouvoir_5b()
        self.assertIsNone(err)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "passage")
        self.assertFalse(result["deja_traite"])

    def test_source_reste_annee_source(self):
        self._promouvoir_5b()
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.annee_scolaire_id, self.annee_src.id,
                         "L'année de l'inscription source ne doit pas changer")
        self.assertEqual(src.classe_id, self.cls_6a.id,
                         "La classe de l'inscription source ne doit pas changer")

    def test_source_statut_termine_decision_passage(self):
        self._promouvoir_5b()
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.statut, "termine")
        self.assertEqual(src.decision_fin_annee, "passage")
        self.assertIsNotNone(src.date_sortie)

    def test_nouvelle_inscription_cible_creee(self):
        result, _ = self._promouvoir_5b()
        cible = db.session.get(Inscription, result["inscription_cible_id"])
        self.assertIsNotNone(cible)
        self.assertEqual(cible.annee_scolaire_id, self.annee_cible.id)
        self.assertEqual(cible.classe_id, self.cls_5b.id)
        self.assertEqual(cible.statut, "inscrit")
        self.assertNotEqual(cible.id, self.insc_src.id,
                            "L'inscription cible doit avoir un ID différent de la source")

    def test_ids_inscriptions_distincts(self):
        result, _ = self._promouvoir_5b()
        self.assertNotEqual(result["inscription_source_id"], result["inscription_cible_id"])

    def test_eleve_classe_id_reste_inchange_cible_planifiee(self):
        """Règle critique Phase 2D-1 : cible planifiée → Eleve.classe_id inchangé."""
        self._promouvoir_5b()
        eleve = db.session.get(Eleve, self.moussa.id)
        self.assertEqual(eleve.classe_id, self.cls_6a.id,
                         "Eleve.classe_id ne doit pas changer si l'année cible est planifiée")

    def test_promotion_vers_mauvais_niveau_refuse(self):
        # 6e → 4e : interdit
        cls_4a = _creer_classe(
            self.ecole_a.id, self.annee_cible.id, "4e A",
            niveau_id=self.niv["4E"].id
        )
        _activer_niveaux_pour_annee(
            self.ecole_a.id, self.annee_cible.id,
            [self.niv["5E"], self.niv["6E"], self.niv["4E"], self.niv["TERMINALE"]]
        )
        db.session.commit()
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=cls_4a.id,
        )
        self.assertIsNotNone(err,
            "Passage 6e→4e doit être refusé (mauvais niveau)")

    def test_promotion_vers_terminale_depuis_6e_refuse(self):
        # 6e → Terminale : interdit
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_term_a.id,
        )
        self.assertIsNotNone(err)

    def test_promotion_classe_fermee_refusee(self):
        self.cls_5b.statut = "fermee"
        db.session.commit()
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        self.assertIsNotNone(err)

    def test_promotion_niveau_non_retenu_annee_cible_refuse(self):
        # Désactiver 5E dans l'année cible
        config = AnneeNiveauConfig.query.filter_by(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niv["5E"].id,
        ).first()
        if config:
            config.actif = False
        db.session.commit()
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5a.id,
        )
        self.assertIsNotNone(err)

    def test_promotion_sans_classe_cible_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=None,
        )
        self.assertIsNotNone(err)

    def test_promotion_classe_autre_ecole_refusee(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_b.id,
        )
        self.assertIsNotNone(err)

    def test_cible_active_sync_classe_id(self):
        """Si l'année cible est active, Eleve.classe_id doit être mis à jour."""
        self.annee_cible.statut = "active"
        db.session.commit()
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        db.session.commit()
        eleve = db.session.get(Eleve, self.moussa.id)
        self.assertEqual(eleve.classe_id, self.cls_5b.id,
                         "Eleve.classe_id doit être mis à jour si cible est active")


# ===========================================================================
# Tests : executer_passage_eleve — TERMINALE → passage refusé
# ===========================================================================

class TestPassageTerminale(BasePassageTestCase):

    def test_passage_depuis_terminale_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_term.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_term_a.id,
        )
        self.assertIsNotNone(err,
            "passage depuis Terminale doit être refusé (niveau_suivant=None)")


# ===========================================================================
# Tests : executer_passage_eleve — REDOUBLEMENT
# ===========================================================================

class TestPassageRedoublement(BasePassageTestCase):

    def test_redoublement_ok(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="redoublement",
            classe_cible_id=self.cls_6b_cible.id,
        )
        db.session.commit()
        self.assertIsNone(err)
        self.assertTrue(result["ok"])

    def test_redoublement_source_termine_decision_redoublement(self):
        self._exec_redoublement()
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.statut, "termine")
        self.assertEqual(src.decision_fin_annee, "redoublement")

    def test_redoublement_nouvelle_inscription_meme_niveau(self):
        result, _ = self._exec_redoublement()
        cible = db.session.get(Inscription, result["inscription_cible_id"])
        self.assertEqual(cible.classe_id, self.cls_6b_cible.id)
        self.assertEqual(cible.statut, "inscrit")

    def test_redoublement_mauvais_niveau_refuse(self):
        # 6e → 5e est invalide pour redoublement
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="redoublement",
            classe_cible_id=self.cls_5a.id,
        )
        self.assertIsNotNone(err,
            "Redoublement 6e→5e doit être refusé")

    def test_redoublement_sans_classe_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="redoublement",
        )
        self.assertIsNotNone(err)

    def _exec_redoublement(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="redoublement",
            classe_cible_id=self.cls_6b_cible.id,
        )
        db.session.commit()
        return result, err


# ===========================================================================
# Tests : executer_passage_eleve — TRANSFERT
# ===========================================================================

class TestPassageTransfert(BasePassageTestCase):

    def test_transfert_ok(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="transfert",
            motif_sortie="Transfert vers Lycée Faranah",
        )
        db.session.commit()
        self.assertIsNone(err)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["inscription_cible_id"],
                          "Aucune inscription cible pour un transfert")

    def test_transfert_source_statut_et_decision(self):
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="transfert",
            motif_sortie="Raison",
        )
        db.session.commit()
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.statut, "transfere")
        self.assertEqual(src.decision_fin_annee, "transfert")
        self.assertIsNotNone(src.date_sortie)
        self.assertIsNotNone(src.motif_sortie)

    def test_transfert_aucune_inscription_cible(self):
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="transfert",
        )
        db.session.commit()
        count = Inscription.query.filter_by(
            eleve_id=self.moussa.id,
            annee_scolaire_id=self.annee_cible.id,
        ).count()
        self.assertEqual(count, 0)

    def test_transfert_avec_classe_cible_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="transfert",
            classe_cible_id=self.cls_5a.id,
        )
        self.assertIsNotNone(err)


# ===========================================================================
# Tests : executer_passage_eleve — SORTIE
# ===========================================================================

class TestPassageSortie(BasePassageTestCase):

    def test_sortie_ok(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="sortie",
        )
        db.session.commit()
        self.assertIsNone(err)
        self.assertIsNone(result["inscription_cible_id"])

    def test_sortie_statut_source(self):
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="sortie",
        )
        db.session.commit()
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.statut, "sorti")
        self.assertEqual(src.decision_fin_annee, "sortie")
        self.assertIsNotNone(src.date_sortie)


# ===========================================================================
# Tests : executer_passage_eleve — DIPLÔME
# ===========================================================================

class TestPassageDiplome(BasePassageTestCase):

    def test_diplome_depuis_terminale_ok(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_term.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="diplome",
        )
        db.session.commit()
        self.assertIsNone(err)
        self.assertIsNone(result["inscription_cible_id"])

    def test_diplome_statut_source(self):
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_term.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="diplome",
        )
        db.session.commit()
        src = db.session.get(Inscription, self.insc_term_src.id)
        self.assertEqual(src.statut, "diplome")
        self.assertEqual(src.decision_fin_annee, "diplome")

    def test_diplome_depuis_6e_refuse(self):
        """6e → diplôme doit être refusé : niveau n'est pas terminal."""
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="diplome",
        )
        self.assertIsNotNone(err,
            "diplome depuis 6e doit être refusé")

    def test_diplome_avec_classe_cible_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_term.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="diplome",
            classe_cible_id=self.cls_term_a.id,
        )
        self.assertIsNotNone(err)


# ===========================================================================
# Tests : fin_cycle
# ===========================================================================

class TestPassageFinCycle(BasePassageTestCase):

    def test_fin_cycle_cloture_source_pas_de_cible(self):
        """fin_cycle = clôture source avec statut termine, aucune inscription cible."""
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="fin_cycle",
        )
        db.session.commit()
        self.assertIsNone(err)
        self.assertIsNone(result["inscription_cible_id"])
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.statut, "termine")
        self.assertEqual(src.decision_fin_annee, "fin_cycle")

    def test_fin_cycle_avec_classe_cible_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="fin_cycle",
            classe_cible_id=self.cls_5a.id,
        )
        self.assertIsNotNone(err)


# ===========================================================================
# Tests : SOURCE ARCHIVÉE
# ===========================================================================

class TestSourceArchivee(BasePassageTestCase):

    def test_source_archivee_refusee(self):
        self.annee_src.statut = "archivee"
        db.session.commit()
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        self.assertIsNotNone(err,
            "La modification d'une inscription archivée doit être refusée")


# ===========================================================================
# Tests : LEGACY — classe source sans niveau_id
# ===========================================================================

class TestLegacySansNiveauId(BasePassageTestCase):

    def test_classe_source_sans_niveau_refuse_passage(self):
        # Retirer le niveau_id de la classe source
        self.cls_6a.niveau_id = None
        db.session.commit()
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        self.assertIsNotNone(err,
            "Passage depuis classe sans niveau_id doit être refusé avec message explicite")
        self.assertIn("niveau", err.lower(),
            "Le message d'erreur doit mentionner le niveau")


# ===========================================================================
# Tests : MULTI-ÉCOLES
# ===========================================================================

class TestMultiEcoles(BasePassageTestCase):

    def test_eleve_autre_ecole_refuse(self):
        eleve_b = _creer_eleve(self.ecole_b.id, self.cls_b.id, "Alien", "B")
        db.session.commit()
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve_b.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        self.assertIsNotNone(err)

    def test_annee_cible_autre_ecole_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_b.id,  # École B
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        self.assertIsNotNone(err)

    def test_classe_cible_autre_ecole_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_b.id,  # classe École B
        )
        self.assertIsNotNone(err)


# ===========================================================================
# Tests : IDEMPOTENCE & CONFLIT
# ===========================================================================

class TestIdempotenceConflit(BasePassageTestCase):

    def test_idempotence_meme_classe(self):
        """Double appel identique → deja_traite=True, aucun doublon."""
        executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        db.session.commit()

        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        db.session.commit()

        self.assertIsNone(err)
        self.assertTrue(result["deja_traite"])
        count = Inscription.query.filter_by(
            eleve_id=self.moussa.id,
            annee_scolaire_id=self.annee_cible.id,
        ).count()
        self.assertEqual(count, 1, "Aucun doublon d'inscription ne doit exister")

    def test_conflit_classe_differente(self):
        """Élève déjà inscrit en 5e A → demande 5e B → conflit."""
        _inscrire(self.ecole_a.id, self.moussa.id,
                  self.annee_cible.id, self.cls_5a.id)
        db.session.commit()

        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        self.assertIsNotNone(err, "Conflit doit être signalé")

    def test_conflit_decision_contradictoire(self):
        """Élève déjà inscrit en 5e A → demande redoublement 6e B → conflit."""
        _inscrire(self.ecole_a.id, self.moussa.id,
                  self.annee_cible.id, self.cls_5a.id)
        db.session.commit()

        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="redoublement",
            classe_cible_id=self.cls_6b_cible.id,
        )
        self.assertIsNotNone(err)


# ===========================================================================
# Tests : ATOMICITÉ
# ===========================================================================

class TestAtomicite(BasePassageTestCase):

    def test_sans_erreur_les_deux_mutations_persistent(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_5b.id,
        )
        db.session.commit()

        self.assertIsNone(err)
        # Source clôturée
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.statut, "termine")
        # Cible créée
        cible = Inscription.query.filter_by(
            eleve_id=self.moussa.id,
            annee_scolaire_id=self.annee_cible.id,
        ).first()
        self.assertIsNotNone(cible)

    def test_rollback_si_erreur_validation_pre_mutation(self):
        """Erreur de validation → aucune mutation en base."""
        count_avant = Inscription.query.count()
        src_statut_avant = db.session.get(Inscription, self.insc_src.id).statut

        # Classe cible appartient à l'année source → invalide
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.cls_6a.id,  # année source !
        )
        db.session.commit()

        self.assertIsNotNone(err)
        self.assertEqual(Inscription.query.count(), count_avant,
                         "Aucune inscription ne doit être créée")
        src = db.session.get(Inscription, self.insc_src.id)
        self.assertEqual(src.statut, src_statut_avant,
                         "L'inscription source ne doit pas être modifiée")


# ===========================================================================
# Tests : DÉCISION INCONNUE
# ===========================================================================

class TestDecisionInconnue(BasePassageTestCase):

    def test_decision_invalide_refuse(self):
        result, err = executer_passage_eleve(
            ecole_id=self.ecole_a.id,
            eleve_id=self.moussa.id,
            annee_source_id=self.annee_src.id,
            annee_cible_id=self.annee_cible.id,
            decision="magie_noire",
        )
        self.assertIsNotNone(err)

    def test_preparer_decision_invalide(self):
        result = preparer_passage_eleve(
            self.ecole_a.id, self.moussa.id,
            self.annee_src.id, self.annee_cible.id,
            "invented_decision"
        )
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["blocage"])


if __name__ == "__main__":
    unittest.main()

