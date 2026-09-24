"""
tests/test_annulation_decision_passage.py
=============================================================
Tests unitaires et d'intégration pour le Chantier B :
Annulation d'une décision de passage avant l'activation (Droit au remords)

Couverture :
  1. Annulation réussie d'un passage (suppression inscription cible + reset source)
  2. Annulation réussie d'une décision de sortie/transfert (reset source sans cible)
  3. Refus strict si l'année cible est 'active' ou 'archivee'
  4. Refus strict si l'année source est 'archivee'
  5. Isolation multi-écoles (sécurité anti-cross-tenant)
  6. Route POST /annees/<src>/passage/<cbl>/annuler/<eid> avec redirection & flash
  7. Présence du formulaire/bouton Annuler dans l'UI (passage_annee.html)
  8. Présence du formulaire/bouton Annuler dans l'UI (passage_eleve.html)
=============================================================
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Utilisateur,
)
from app.services.inscriptions_annuelles import get_inscription
from app.services.passage_annee import (
    annuler_decision_passage,
    executer_passage_eleve,
)
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class AnnulationPassageTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-annulation-passage-key"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestAnnulationDecisionPassage(unittest.TestCase):
    def setUp(self):
        self.app = create_app(AnnulationPassageTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Établissement
        self.ecole = Ecole(
            nom="École Pilote Klasora",
            adresse="Niamey",
            telephone="90000000",
            email="contact@ecolepilote.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # 2. Utilisateur Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Moussa",
            email="directeur@ecolepilote.ne",
            mot_de_passe=generate_password_hash("Secret123!"),
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(self.admin)
        db.session.flush()

        # 3. Niveaux scolaires (CM2 -> 6e)
        self.niveau_6e = NiveauScolaire(code="6E", nom="6ème", cycle="college", ordre=2)
        db.session.add(self.niveau_6e)
        db.session.flush()

        self.niveau_cm2 = NiveauScolaire(
            code="CM2", nom="CM2", cycle="primaire", ordre=1, niveau_suivant_id=self.niveau_6e.id
        )
        db.session.add(self.niveau_cm2)
        db.session.flush()

        # 4. Années scolaires
        self.annee_source = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 1),
            date_fin=date(2025, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        self.annee_cible = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="planifiee",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.annee_source, self.annee_cible])
        db.session.flush()

        # Config niveaux pour l'année cible
        cfg1 = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niveau_6e.id,
            actif=True,
        )
        cfg2 = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niveau_cm2.id,
            actif=True,
        )
        db.session.add_all([cfg1, cfg2])

        # 5. Classes
        self.classe_source = Classe(
            nom="CM2 A",
            niveau="CM2",
            niveau_id=self.niveau_cm2.id,
            capacite=30,
            capacite_max=30,
            statut="ouverte",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
        )
        self.classe_cible = Classe(
            nom="6ème A",
            niveau="6ème",
            niveau_id=self.niveau_6e.id,
            capacite=30,
            capacite_max=30,
            statut="ouverte",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
        )
        db.session.add_all([self.classe_source, self.classe_cible])
        db.session.flush()

        # 6. Élève avec inscription source
        self.eleve = Eleve(
            nom="Bello",
            prenom="Ibrahim",
            genre="M",
            date_naissance=date(2013, 8, 14),
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
            statut="actif",
        )
        db.session.add(self.eleve)
        db.session.flush()

        self.insc_source = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.annee_source.id,
            classe_id=self.classe_source.id,
            statut="inscrit",
            date_inscription=datetime.utcnow(),
            frais_annuels=150000.0,
        )
        db.session.add(self.insc_source)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['user_id'] = self.admin.id
            sess['ecole_id'] = self.ecole.id
            sess['annee_active_id'] = self.annee_source.id
            sess['annee_consultee_id'] = self.annee_source.id

    def test_01_annuler_passage_avec_classe_cible(self):
        """Annule avec succès une décision de passage : supprime l'inscription cible et réinitialise l'inscription source."""
        # 1. Exécuter un passage
        res, err = executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.classe_cible.id,
        )
        self.assertIsNone(err)
        db.session.commit()

        # Vérifier l'état post-passage
        insc_cible = get_inscription(self.eleve, self.annee_cible)
        self.assertIsNotNone(insc_cible)
        self.assertEqual(insc_cible.statut, "preinscrit")
        db.session.refresh(self.insc_source)
        self.assertEqual(self.insc_source.decision_fin_annee, "passage")

        # 2. Annuler la décision
        ok, msg = annuler_decision_passage(
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertTrue(ok)
        self.assertIn("annulée avec succès", msg)

        # 3. Vérifier les répercussions
        insc_cible_apres = get_inscription(self.eleve, self.annee_cible)
        self.assertIsNone(insc_cible_apres, "L'inscription cible doit avoir été supprimée")

        db.session.refresh(self.insc_source)
        self.assertIsNone(self.insc_source.decision_fin_annee, "decision_fin_annee doit être réinitialisé à None")
        self.assertIsNone(self.insc_source.motif_sortie)
        self.assertIsNone(self.insc_source.date_sortie)
        self.assertEqual(self.insc_source.statut, "inscrit")

    def test_02_annuler_decision_sortie_sans_classe_cible(self):
        """Annule une décision de sortie (sans inscription cible) : réinitialise l'inscription source."""
        res, err = executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="sortie",
            motif_sortie="Déménagement familial",
        )
        self.assertIsNone(err)
        db.session.commit()

        db.session.refresh(self.insc_source)
        self.assertEqual(self.insc_source.decision_fin_annee, "sortie")
        self.assertEqual(self.insc_source.statut, "sorti")
        self.assertEqual(self.insc_source.motif_sortie, "Déménagement familial")

        # Annulation
        ok, msg = annuler_decision_passage(
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertTrue(ok)

        db.session.refresh(self.insc_source)
        self.assertIsNone(self.insc_source.decision_fin_annee)
        self.assertIsNone(self.insc_source.motif_sortie)
        self.assertIsNone(self.insc_source.date_sortie)
        self.assertEqual(self.insc_source.statut, "inscrit")

    def test_03_refus_si_annee_cible_non_planifiee(self):
        """Refuse l'annulation si l'année cible est déjà active ou archivée."""
        # Enregistrer un passage
        executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.classe_cible.id,
        )
        db.session.commit()

        # Passer l'année cible à 'active'
        self.annee_cible.statut = "active"
        db.session.commit()

        ok, msg = annuler_decision_passage(
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("planifiée", msg.lower())

    def test_04_refus_si_annee_source_archivee(self):
        """Refuse l'annulation si l'année source est archivée."""
        self.annee_source.statut = "archivee"
        db.session.commit()

        ok, msg = annuler_decision_passage(
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=self.ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("archiv", msg.lower())

    def test_05_isolation_multi_ecoles(self):
        """Refuse l'annulation pour un élève appartenant à un autre établissement."""
        autre_ecole = Ecole(nom="Autre École", telephone="91111111", statut="actif")
        db.session.add(autre_ecole)
        db.session.commit()

        ok, msg = annuler_decision_passage(
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            ecole_id=autre_ecole.id,
        )
        self.assertFalse(ok)
        self.assertIn("introuvable", msg.lower())

    def test_06_route_post_annuler_decision_eleve(self):
        """La route POST annuler_decision_eleve réinitialise l'élève et redirige avec message de confirmation."""
        self._login_admin()

        # Enregistrer une décision
        executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.classe_cible.id,
        )
        db.session.commit()

        # Appel de la route POST d'annulation
        resp = self.client.post(
            f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/annuler/{self.eleve.id}",
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Décision annulée avec succès", html)

        # Vérification en base
        insc_cible = get_inscription(self.eleve, self.annee_cible)
        self.assertIsNone(insc_cible)
        db.session.refresh(self.insc_source)
        self.assertIsNone(self.insc_source.decision_fin_annee)

    def test_07_ui_bouton_annuler_passage_annee(self):
        """Le template passage_annee.html affiche le bouton Annuler pour les élèves traités tant que l'année cible est planifiée."""
        self._login_admin()

        # Avant décision : bouton Traiter visible, pas de bouton Annuler
        resp = self.client.get(f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Traiter", html)
        self.assertNotIn("Annuler", html)

        # Exécuter décision
        executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.classe_cible.id,
        )
        db.session.commit()

        # Après décision : bouton Annuler présent dans le tableau
        resp = self.client.get(f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Annuler", html)
        self.assertIn(f"/passage/{self.annee_cible.id}/annuler/{self.eleve.id}", html)

    def test_08_ui_bouton_annuler_passage_eleve(self):
        """Le template passage_eleve.html propose le bouton d'annulation rapide pour un élève déjà traité."""
        self._login_admin()

        # Exécuter décision
        executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.classe_cible.id,
        )
        db.session.commit()

        resp = self.client.get(f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}/eleves/{self.eleve.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Annuler la décision", html)
        self.assertIn(f"/passage/{self.annee_cible.id}/annuler/{self.eleve.id}", html)


if __name__ == "__main__":
    unittest.main()

