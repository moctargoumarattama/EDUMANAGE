import os
import sqlite3
import unittest
from datetime import date, datetime

from flask import template_rendered
from flask_login import login_user

from app import create_app, db
from app.config import Config
from app.models import (
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    Paiement,
    Utilisateur,
)
from app.services.inscriptions_annuelles import (
    creer_inscription_annuelle,
    modifier_inscription_annuelle,
)
from app.services.paiements_annuels import (
    enregistrer_paiement,
    get_finances_inscription,
    get_inscriptions_paiements,
    get_paiements_annee,
    modifier_frais_inscription,
    supprimer_paiement_securise,
    valider_mutation_paiement,
)


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class Phase3BPaiementsAnnuelsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Écoles
        self.ecole_a = Ecole(nom="École A Test", statut="actif")
        self.ecole_b = Ecole(nom="École B Test", statut="actif")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.flush()

        # Années scolaires École A
        self.archivee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 7, 31),
            statut="archivee",
            ecole_id=self.ecole_a.id,
        )
        self.active = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_a.id,
        )
        self.planifiee = AnneeScolaire(
            nom="2027-2028",
            date_debut=date(2027, 9, 1),
            date_fin=date(2028, 7, 31),
            statut="planifiee",
            ecole_id=self.ecole_a.id,
        )
        # Année scolaire École B
        self.active_b = AnneeScolaire(
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 7, 31),
            statut="active",
            ecole_id=self.ecole_b.id,
        )
        db.session.add_all([self.archivee, self.active, self.planifiee, self.active_b])
        db.session.flush()

        # Classes
        self.classe_archive = Classe(
            nom="6e A", niveau="6e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.archivee.id
        )
        self.classe_active = Classe(
            nom="5e A", niveau="5e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.active.id
        )
        self.classe_plan = Classe(
            nom="4e A", niveau="4e", ecole_id=self.ecole_a.id, annee_scolaire_id=self.planifiee.id
        )
        self.classe_b = Classe(
            nom="5e B", niveau="5e", ecole_id=self.ecole_b.id, annee_scolaire_id=self.active_b.id
        )
        db.session.add_all([self.classe_archive, self.classe_active, self.classe_plan, self.classe_b])
        db.session.flush()

        # Utilisateurs
        self.admin = Utilisateur(
            nom="Admin A", email="admin3b@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_a.id
        )
        self.parent = Utilisateur(
            nom="Parent A", email="parent3b@test.local", mot_de_passe="x", role="parent", ecole_id=self.ecole_a.id
        )
        self.prof_user = Utilisateur(
            nom="Prof A", email="prof3b@test.local", mot_de_passe="x", role="professeur", ecole_id=self.ecole_a.id
        )
        self.admin_b = Utilisateur(
            nom="Admin B", email="adminb3b@test.local", mot_de_passe="x", role="admin", ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.admin, self.parent, self.prof_user, self.admin_b])
        db.session.flush()

        # Élèves
        self.eleve = Eleve(
            nom="Bah",
            prenom="Amadou",
            date_naissance=date(2013, 4, 10),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            parent_id=self.parent.id,
            frais_annuels=100000.0,
        )
        self.eleve_2 = Eleve(
            nom="Camara",
            prenom="Fatou",
            date_naissance=date(2013, 8, 15),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_active.id,
            parent_id=self.parent.id,
            frais_annuels=80000.0,
        )
        self.eleve_b = Eleve(
            nom="Sow",
            prenom="Ibrahim",
            date_naissance=date(2013, 2, 20),
            ecole_id=self.ecole_b.id,
            classe_id=self.classe_b.id,
            frais_annuels=50000.0,
        )
        db.session.add_all([self.eleve, self.eleve_2, self.eleve_b])
        db.session.flush()

        # Inscriptions annuelles avec frais
        self.insc_archive = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.archivee.id,
            classe_id=self.classe_archive.id,
            frais_annuels=90000.0,
        )
        self.insc_active = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.active.id,
            classe_id=self.classe_active.id,
            frais_annuels=100000.0,
        )
        self.insc_active_2 = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_2.id,
            annee_scolaire_id=self.active.id,
            classe_id=self.classe_active.id,
            frais_annuels=80000.0,
        )
        self.insc_planifiee = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            annee_scolaire_id=self.planifiee.id,
            classe_id=self.classe_plan.id,
            frais_annuels=110000.0,
        )
        self.insc_b = Inscription(
            ecole_id=self.ecole_b.id,
            eleve_id=self.eleve_b.id,
            annee_scolaire_id=self.active_b.id,
            classe_id=self.classe_b.id,
            frais_annuels=50000.0,
        )
        db.session.add_all([self.insc_archive, self.insc_active, self.insc_active_2, self.insc_planifiee, self.insc_b])
        db.session.flush()

        # Paiements
        self.p_archive = Paiement(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            inscription_id=self.insc_archive.id,
            montant=90000.0,
            mois="Octobre",
            annee=2025,
            mode_paiement="Espèces",
            reference="REC-2025-01",
            statut="payé",
            date_paiement=datetime(2025, 10, 5, 10, 0),
        )
        self.p_active_partiel = Paiement(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            inscription_id=self.insc_active.id,
            montant=40000.0,
            mois="Novembre",
            annee=2026,
            mode_paiement="Virement",
            reference="REC-2026-01",
            statut="payé",
            date_paiement=datetime(2026, 11, 2, 11, 30),
        )
        db.session.add_all([self.p_archive, self.p_active_partiel])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # =========================================================================
    # GROUPE 1 : Modèle et snapshot des frais annuels
    # =========================================================================

    def test_01_inscription_snapshot_frais_annuels_depuis_eleve(self):
        """1. Inscription sans frais explicites prend le snapshot de eleve.frais_annuels."""
        insc, _ = creer_inscription_annuelle(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_2.id,
            classe_id=self.classe_plan.id,
            annee_scolaire_id=self.planifiee.id,
        )
        self.assertIsNotNone(insc)
        self.assertEqual(insc.frais_annuels, 80000.0)

    def test_02_inscription_frais_annuels_explicite(self):
        """2. Inscription avec frais explicites stocke le montant spécifié."""
        insc, _ = creer_inscription_annuelle(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve_2.id,
            classe_id=self.classe_archive.id,
            annee_scolaire_id=self.archivee.id,
            frais_annuels=75000.0,
            allow_archived=True,
        )
        self.assertIsNotNone(insc)
        self.assertEqual(insc.frais_annuels, 75000.0)

    def test_03_eleve_plusieurs_annees_frais_differents(self):
        """3. Un élève a des frais indépendants selon les années scolaires."""
        self.assertEqual(self.insc_archive.frais_annuels, 90000.0)
        self.assertEqual(self.insc_active.frais_annuels, 100000.0)
        self.assertEqual(self.insc_planifiee.frais_annuels, 110000.0)

    def test_04_paiement_foreign_key_inscription_id(self):
        """4. Paiement.inscription_id pointe sur Inscription et résout l'année scolaire."""
        self.assertIsNotNone(self.p_active_partiel.inscription)
        self.assertEqual(self.p_active_partiel.inscription.annee_scolaire.nom, "2026-2027")
        self.assertEqual(self.p_active_partiel.inscription.classe.nom, "5e A")

    def test_05_paiement_inscription_backref(self):
        """5. Inscription.paiements liste les versements attachés."""
        self.assertEqual(len(self.insc_active.paiements), 1)
        self.assertEqual(self.insc_active.paiements[0].id, self.p_active_partiel.id)

    def test_06_serialization_to_dict(self):
        """6. Paiement.to_dict et Inscription.to_dict contiennent les nouveaux champs 3B."""
        p_dict = self.p_active_partiel.to_dict()
        self.assertIn("inscription_id", p_dict)
        self.assertEqual(p_dict["inscription_id"], self.insc_active.id)

        i_dict = self.insc_active.to_dict()
        self.assertIn("frais_annuels", i_dict)
        self.assertEqual(i_dict["frais_annuels"], 100000.0)

    # =========================================================================
    # GROUPE 2 : Service d'encaissement (enregistrer_paiement, valider_mutation_paiement)
    # =========================================================================

    def test_07_encaissement_annee_active_succes(self):
        """7. Encaissement sur année active valide la transaction et fixe inscription_id."""
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve.id,
            montant=60000.0,
            mois="Décembre",
            annee_civile=2026,
            mode_paiement="Espèces",
            reference="REC-2026-02",
        )
        self.assertIsNone(err)
        self.assertIsNotNone(paiement)
        self.assertEqual(paiement.inscription_id, self.insc_active.id)
        self.assertEqual(paiement.montant, 60000.0)

    def test_08_encaissement_annee_planifiee_interdit(self):
        """8. Encaissement sur année planifiée est strictement rejeté."""
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.planifiee,
            user=self.admin,
            eleve_id=self.eleve.id,
            montant=20000.0,
            mois="Septembre",
            annee_civile=2027,
        )
        self.assertIsNone(paiement)
        self.assertIsNotNone(err)
        self.assertIn("active", err.lower())

    def test_09_encaissement_annee_archivee_interdit(self):
        """9. Encaissement sur année archivée est rejeté (lecture seule)."""
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.archivee,
            user=self.admin,
            eleve_id=self.eleve.id,
            montant=15000.0,
            mois="Janvier",
            annee_civile=2026,
        )
        self.assertIsNone(paiement)
        self.assertIsNotNone(err)
        self.assertIn("archiv", err.lower())

    def test_10_encaissement_eleve_sans_inscription_annee_refuse(self):
        """10. Encaissement d'un élève non inscrit dans l'année consultée est refusé."""
        eleve_non_inscrit = Eleve(
            nom="Nouveau",
            prenom="Jean",
            date_naissance=date(2014, 1, 1),
            ecole_id=self.ecole_a.id,
        )
        db.session.add(eleve_non_inscrit)
        db.session.flush()

        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=eleve_non_inscrit.id,
            montant=30000.0,
            mois="Janvier",
            annee_civile=2027,
        )
        self.assertIsNone(paiement)
        self.assertIsNotNone(err)
        self.assertIn("inscrit", err.lower())

    def test_11_encaissement_montant_invalide(self):
        """11. Montant nul ou négatif est rejeté."""
        p1, err1 = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve.id,
            montant=0,
            mois="Janvier",
            annee_civile=2027,
        )
        self.assertIsNotNone(err1)

        p2, err2 = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve.id,
            montant=-5000.0,
            mois="Janvier",
            annee_civile=2027,
        )
        self.assertIsNotNone(err2)

    def test_12_encaissement_role_non_autorise(self):
        """12. Parent ou utilisateur non habilité ne peut pas enregistrer un paiement."""
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.parent,
            eleve_id=self.eleve.id,
            montant=10000.0,
            mois="Janvier",
            annee_civile=2027,
        )
        self.assertIsNone(paiement)
        self.assertIsNotNone(err)
        self.assertIn("autoris", err.lower())

    def test_13_paiements_partiels_cumulatifs(self):
        """13. Plusieurs paiements partiels dans la même année s'accumulent correctement."""
        # Premier versement de 40 000 déjà dans setUp.
        # Deuxième versement de 30 000
        p2, err = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve.id,
            montant=30000.0,
            mois="Décembre",
            annee_civile=2026,
        )
        self.assertIsNone(err)
        db.session.commit()

        fin = get_finances_inscription(self.insc_active)
        self.assertEqual(fin["total_paye"], 70000.0)
        self.assertEqual(fin["reste_a_payer"], 30000.0)
        self.assertEqual(fin["statut_solde"], "partiel")

    # =========================================================================
    # GROUPE 3 : Calculs financiers et soldes (get_finances_inscription)
    # =========================================================================

    def test_14_solde_aucun_versement(self):
        """14. Inscription sans versement : payé 0, reste = frais, statut 'aucun'."""
        fin = get_finances_inscription(self.insc_active_2)
        self.assertEqual(fin["total_paye"], 0.0)
        self.assertEqual(fin["reste_a_payer"], 80000.0)
        self.assertEqual(fin["pourcentage_paye"], 0.0)
        self.assertEqual(fin["statut_solde"], "aucun")

    def test_15_solde_partiel(self):
        """15. Inscription avec versement partiel (40 000 / 100 000) donne 40% et 'partiel'."""
        fin = get_finances_inscription(self.insc_active)
        self.assertEqual(fin["total_paye"], 40000.0)
        self.assertEqual(fin["reste_a_payer"], 60000.0)
        self.assertEqual(fin["pourcentage_paye"], 40.0)
        self.assertEqual(fin["statut_solde"], "partiel")

    def test_16_solde_complet(self):
        """16. Inscription totalement soldée : reste = 0, statut 'complet'."""
        fin = get_finances_inscription(self.insc_archive)
        self.assertEqual(fin["total_paye"], 90000.0)
        self.assertEqual(fin["reste_a_payer"], 0.0)
        self.assertEqual(fin["pourcentage_paye"], 100.0)
        self.assertEqual(fin["statut_solde"], "complet")

    def test_17_solde_surplus_ne_devient_pas_negatif(self):
        """17. Un versement supérieur au solde ne rend pas le reste négatif (max(0, ...))."""
        p_surplus = Paiement(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            inscription_id=self.insc_active.id,
            montant=80000.0,  # 40k + 80k = 120k > 100k
            mois="Avril",
            annee=2027,
            statut="payé",
        )
        db.session.add(p_surplus)
        db.session.commit()

        fin = get_finances_inscription(self.insc_active)
        self.assertEqual(fin["total_paye"], 120000.0)
        self.assertEqual(fin["reste_a_payer"], 0.0)
        self.assertEqual(fin["statut_solde"], "complet")

    def test_18_solde_frais_zero_gere_sans_division_par_zero(self):
        """18. Inscription avec frais_annuels = 0 ne lève pas de ZeroDivisionError."""
        self.insc_active_2.frais_annuels = 0.0
        db.session.commit()

        fin = get_finances_inscription(self.insc_active_2)
        self.assertEqual(fin["pourcentage_paye"], 100.0)
        self.assertEqual(fin["reste_a_payer"], 0.0)
        self.assertEqual(fin["statut_solde"], "complet")

    def test_19_isolation_soldes_entre_deux_annees(self):
        """19. Les paiements de 2026-2027 n'impactent pas les soldes de 2025-2026."""
        fin_archive = get_finances_inscription(self.insc_archive)
        fin_active = get_finances_inscription(self.insc_active)

        self.assertEqual(fin_archive["total_paye"], 90000.0)
        self.assertEqual(fin_archive["frais_annuels"], 90000.0)
        self.assertEqual(fin_active["total_paye"], 40000.0)
        self.assertEqual(fin_active["frais_annuels"], 100000.0)

    # =========================================================================
    # GROUPE 4 : Modification et suppression de paiements & frais
    # =========================================================================

    def test_20_suppression_paiement_annee_active_autorisee(self):
        """20. Suppression d'un paiement de l'année active par un admin réussit."""
        p = Paiement(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            inscription_id=self.insc_active.id,
            montant=10000.0,
            mois="Décembre",
            annee=2026,
            statut="payé",
        )
        db.session.add(p)
        db.session.commit()

        ok, err = supprimer_paiement_securise(self.ecole_a.id, self.active, p.id, self.admin)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_21_suppression_paiement_annee_archivee_bloquee(self):
        """21. Suppression d'un paiement d'une année archivée est strictement bloquée."""
        ok, err = supprimer_paiement_securise(self.ecole_a.id, self.archivee, self.p_archive.id, self.admin)
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertIn("archiv", err.lower())

    def test_22_suppression_paiement_annee_planifiee_bloquee(self):
        """22. Suppression d'un paiement sur année planifiée est bloquée."""
        ok, err = supprimer_paiement_securise(self.ecole_a.id, self.planifiee, self.p_active_partiel.id, self.admin)
        self.assertFalse(ok)
        self.assertIsNotNone(err)

    def test_23_modification_frais_annee_planifiee_autorisee(self):
        """23. Préparation des frais : modifier Inscription.frais_annuels en année planifiée est permis."""
        ok, err = modifier_frais_inscription(self.ecole_a.id, self.planifiee, self.insc_planifiee.id, 125000.0, self.admin)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(self.insc_planifiee.frais_annuels, 125000.0)

    def test_24_modification_frais_annee_active_autorisee(self):
        """24. Ajustement des frais : modifier Inscription.frais_annuels en année active est permis."""
        ok, err = modifier_frais_inscription(self.ecole_a.id, self.active, self.insc_active.id, 105000.0, self.admin)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(self.insc_active.frais_annuels, 105000.0)

    def test_25_modification_frais_annee_archivee_bloquee(self):
        """25. Modification des frais en année archivée est strictement interdite."""
        ok, err = modifier_frais_inscription(self.ecole_a.id, self.archivee, self.insc_archive.id, 95000.0, self.admin)
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertIn("archiv", err.lower())

    # =========================================================================
    # GROUPE 5 : Décorrélation du cache élève (Eleve.classe_id) et intégrité historique
    # =========================================================================

    def test_26_changement_classe_cache_ne_modifie_pas_recu_historique(self):
        """26. Changer Eleve.classe_id ne modifie pas la classe historique sur le reçu."""
        # L'élève passe dans une nouvelle classe en cache
        autre_classe = Classe(nom="Classe Autre", niveau="Autre", ecole_id=self.ecole_a.id)
        db.session.add(autre_classe)
        db.session.flush()

        self.eleve.classe_id = autre_classe.id
        db.session.commit()

        # Le paiement historique conserve sa classe d'inscription (6e A)
        self.assertEqual(self.p_archive.inscription.classe.nom, "6e A")
        # Le paiement actif conserve sa classe d'inscription (5e A)
        self.assertEqual(self.p_active_partiel.inscription.classe.nom, "5e A")

    def test_27_classe_cache_null_ne_casse_pas_recu_historique(self):
        """27. Élève sorti (classe_id = NULL) ne casse pas l'affichage du reçu."""
        self.eleve.classe_id = None
        db.session.commit()

        self.assertIsNone(self.eleve.classe_id)
        self.assertEqual(self.p_archive.inscription.classe.nom, "6e A")
        self.assertEqual(self.p_archive.inscription.annee_scolaire.nom, "2025-2026")

    def test_28_annee_scolaire_recu_est_annee_inscription(self):
        """28. L'année scolaire du reçu provient de inscription.annee_scolaire."""
        self.assertEqual(self.p_archive.inscription.annee_scolaire.nom, "2025-2026")
        self.assertEqual(self.p_active_partiel.inscription.annee_scolaire.nom, "2026-2027")

    def test_29_paiement_legacy_sans_inscription_id_fallback(self):
        """29. Un paiement hérité sans inscription_id ne lève pas d'exception."""
        p_legacy = Paiement(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            inscription_id=None,
            montant=25000.0,
            mois="Mars",
            annee=2025,
            statut="payé",
        )
        db.session.add(p_legacy)
        db.session.commit()

        self.assertIsNone(p_legacy.inscription_id)
        self.assertIsNone(p_legacy.inscription)
        # Accès direct aux informations de l'élève garanti
        self.assertEqual(p_legacy.eleve.nom, "Bah")

    # =========================================================================
    # GROUPE 6 : Isolation multi-école
    # =========================================================================

    def test_30_isolation_encaissement_autre_ecole(self):
        """30. L'admin de l'école A ne peut pas encaisser pour un élève de l'école B."""
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole_a.id,
            annee=self.active,
            user=self.admin,
            eleve_id=self.eleve_b.id,
            montant=20000.0,
            mois="Octobre",
            annee_civile=2026,
        )
        self.assertIsNone(paiement)
        self.assertIsNotNone(err)

    def test_31_isolation_consultation_autre_ecole(self):
        """31. get_paiements_annee pour l'école A ne contient aucun paiement de l'école B."""
        # Création d'un paiement dans l'école B
        p_b = Paiement(
            ecole_id=self.ecole_b.id,
            eleve_id=self.eleve_b.id,
            inscription_id=self.insc_b.id,
            montant=50000.0,
            mois="Octobre",
            annee=2026,
            statut="payé",
        )
        db.session.add(p_b)
        db.session.commit()

        paiements_a = get_paiements_annee(self.ecole_a.id, self.active, self.admin)
        ids_a = [p.id for p in paiements_a]
        self.assertNotIn(p_b.id, ids_a)

    def test_32_suppression_paiement_autre_ecole_refusee(self):
        """32. L'admin de l'école A ne peut pas supprimer un paiement de l'école B."""
        p_b = Paiement(
            ecole_id=self.ecole_b.id,
            eleve_id=self.eleve_b.id,
            inscription_id=self.insc_b.id,
            montant=50000.0,
            mois="Octobre",
            annee=2026,
            statut="payé",
        )
        db.session.add(p_b)
        db.session.commit()

        ok, err = supprimer_paiement_securise(self.ecole_a.id, self.active, p_b.id, self.admin)
        self.assertFalse(ok)
        self.assertIsNotNone(err)

    # =========================================================================
    # GROUPE 7 : Règle 2C-5D et consultation de session
    # =========================================================================

    def test_33_paiements_consulte_annee_session(self):
        """33. /paiements respecte l'année stockée en session sans la muter."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.archivee.id

        res = client.get("/paiements")
        self.assertEqual(res.status_code, 200)

        with client.session_transaction() as sess:
            # La règle 2C-5D exige que session["annee_consultee"] n'ait pas été modifiée par /paiements
            self.assertEqual(sess["annee_consultee"], self.archivee.id)

    def test_34_paiements_fallback_annee_active_si_session_vide(self):
        """34. Si aucune année en session, /paiements se rabat sur l'année active."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            if "annee_consultee" in sess:
                del sess["annee_consultee"]

        res = client.get("/paiements")
        self.assertEqual(res.status_code, 200)

    # =========================================================================
    # GROUPE 8 : Routes HTTP & Interface (/paiements, reçus, exports)
    # =========================================================================

    def test_35_http_get_paiements_annee_active(self):
        """35. GET /paiements en année active affiche le bouton 'Nouveau paiement'."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.active.id

        res = client.get("/paiements")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Nouveau paiement", html)
        self.assertIn("btn-quick-pay", html)

    def test_36_http_get_paiements_annee_archivee_lecture_seule(self):
        """36. GET /paiements en année archivée affiche la bannière lecture seule."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.archivee.id

        res = client.get("/paiements")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("archiv", html.lower())
        self.assertIn("lecture seule", html.lower())
        self.assertNotIn("btn-quick-pay", html)

    def test_37_http_get_paiements_annee_planifiee_preparation(self):
        """37. GET /paiements en année planifiée affiche la bannière de préparation."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.planifiee.id

        res = client.get("/paiements")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("préparation", html)
        self.assertNotIn("btn-quick-pay", html)

    def test_38_http_post_paiement_annee_active_succes(self):
        """38. POST /paiements sur année active enregistre le versement avec succès."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.active.id

        data = {
            "eleve_id": self.eleve_2.id,
            "montant": 50000.0,
            "mois": "Novembre",
            "annee": 2026,
            "mode_paiement": "Espèces",
            "reference": "TEST-HTTP-01",
        }
        res = client.post("/paiements", data=data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        p = Paiement.query.filter_by(reference="TEST-HTTP-01").first()
        self.assertIsNotNone(p)
        self.assertEqual(p.inscription_id, self.insc_active_2.id)

    def test_39_http_post_paiement_annee_archivee_rejete(self):
        """39. POST /paiements sur année archivée est rejeté avec alerte."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.archivee.id

        data = {
            "eleve_id": self.eleve.id,
            "montant": 10000.0,
            "mois": "Janvier",
            "annee": 2026,
            "mode_paiement": "Espèces",
        }
        res = client.post("/paiements", data=data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("archiv", html.lower())

    def test_40_http_post_paiement_annee_planifiee_rejete(self):
        """40. POST /paiements sur année planifiée est rejeté avec avertissement."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.planifiee.id

        data = {
            "eleve_id": self.eleve.id,
            "montant": 20000.0,
            "mois": "Septembre",
            "annee": 2027,
            "mode_paiement": "Espèces",
        }
        res = client.post("/paiements", data=data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("active", html.lower())

    def test_41_http_supprimer_paiement_archive_rejete(self):
        """41. POST /paiements/<id>/supprimer pour paiement archivé est refusé."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.archivee.id

        res = client.post(f"/paiements/{self.p_archive.id}/supprimer", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("archiv", html.lower())

        # Le paiement doit toujours exister
        p = Paiement.query.get(self.p_archive.id)
        self.assertIsNotNone(p)

    def test_42_http_recu_paiement_html(self):
        """42. GET /paiements/<id> affiche le reçu avec classe historique et année scolaire."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/paiements/{self.p_archive.id}")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("6e A", html)
        self.assertIn("2025-2026", html)

    def test_43_http_generer_recu_pdf(self):
        """43. GET /paiements/<id>/pdf génère un document PDF avec succès."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        res = client.get(f"/paiements/{self.p_active_partiel.id}/pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "application/pdf")

    def test_44_http_export_excel(self):
        """44. GET /paiements/export/excel génère un tableur Excel pour l'année consultée."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["annee_consultee"] = self.active.id

        res = client.get("/paiements/export/excel")
        self.assertEqual(res.status_code, 200)
        self.assertIn("spreadsheetml", res.content_type)

    def test_45_parent_voit_uniquement_ses_enfants(self):
        """45. GET /parent/paiements n'affiche que les règlements des enfants du parent."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(self.parent.id)

        res = client.get("/parent/paiements")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("Amadou", html)
        self.assertNotIn("Ibrahim", html)

    # =========================================================================
    # GROUPE 9 : Base réelle SQLite et intégrité Alembic
    # =========================================================================

    def test_46_base_migree_reelle_colonnes_presentes(self):
        """46. La vraie base SQLite instance/ecole.db possède inscription_id et frais_annuels."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Vérification table paiement
        cur.execute("PRAGMA table_info(paiement)")
        cols_paiement = {row[1] for row in cur.fetchall()}
        self.assertIn("inscription_id", cols_paiement, "Colonne inscription_id manquante dans paiement")

        # Vérification table inscriptions
        cur.execute("PRAGMA table_info(inscriptions)")
        cols_inscriptions = {row[1] for row in cur.fetchall()}
        self.assertIn("frais_annuels", cols_inscriptions, "Colonne frais_annuels manquante dans inscriptions")

        conn.close()

    def test_47_base_migree_alembic_head_15ae5fdad90d(self):
        """47. La révision Alembic de la base ecole.db est bien 15ae5fdad90d (head 3B)."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        rows = cur.fetchall()
        conn.close()

        versions = {row[0] for row in rows}
        self.assertTrue(
            any(v in {"15ae5fdad90d", "8ab121cfcb45", "9bc234dfde56"} for v in versions),
            f"La révision 15ae5fdad90d ou supérieure doit être active. Révisions actuelles: {versions}",
        )

    def test_48_base_migree_sqlite_foreign_key_restrict(self):
        """48. La contrainte de clé étrangère sur paiement.inscription_id est ON DELETE RESTRICT."""
        db_path = os.path.join(os.path.dirname(__file__), "..", "instance", "ecole.db")
        if not os.path.exists(db_path):
            self.skipTest("Base ecole.db absente — test ignoré")

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_key_list(paiement)")
        fks = cur.fetchall()
        conn.close()

        insc_fks = [fk for fk in fks if fk[2] == "inscriptions" and fk[3] == "inscription_id"]
        self.assertTrue(len(insc_fks) > 0, "FK vers inscriptions(id) introuvable dans paiement")
        on_delete = insc_fks[0][6].upper()
        self.assertEqual(on_delete, "RESTRICT", f"La politique FK doit être RESTRICT, obtenu: {on_delete}")

