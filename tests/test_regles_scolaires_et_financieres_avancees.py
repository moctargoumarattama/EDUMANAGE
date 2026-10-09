"""
Tests unitaires pour les règles scolaires et financières avancées (Bloc 2 - KLASORA):
1. Moyenne annuelle & Passage : calcul réglementaire (division par nb_attendues) et statut 'Dossier Incomplet'
   bloquant la suggestion de passage.
2. Transferts / Sorties : annulation formelle de la préinscription N+1 et levée du blocage 'déjà traité'.
3. Propagation des frais & Remises : synchronisation instantanée de l'élève vers son Inscription active.
4. Algorithme de relance intelligent : détection d'impayé au prorata d'échéance insensible aux micro-versements.
"""
from datetime import date, datetime
import unittest

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Bulletin,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Paiement,
    PeriodeBulletin,
    Utilisateur,
)
from app.services import generer_alertes_automatiques
from app.services.bulletins_annuels import calculer_moyenne_annuelle_reglementaire
from app.services.inscriptions_annuelles import transferer_ou_radier_eleve
from app.services.paiements_annuels import (
    calculer_retard_echeancier_inscription,
    obtenir_synthese_financiere_eleve,
    synchroniser_frais_et_remises_inscription,
)
from app.services.passage_annee import (
    evaluer_deliberation_annuelle,
    executer_passage_eleve,
    get_deliberations_annuelles_eleves,
)


class AdvancedRulesTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-advanced-rules"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestReglesScolairesEtFinancieresAvancees(unittest.TestCase):
    def setUp(self):
        self.app = create_app(AdvancedRulesTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Établissement
        self.ecole = Ecole(
            nom="Complexe Scolaire Test",
            adresse="Niamey",
            telephone="90000000",
            email="contact@complexe-test.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # 2. Utilisateur Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Admin",
            email="admin@complexe-test.ne",
            mot_de_passe=generate_password_hash("password123"),
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.admin)

        # 3. Niveaux scolaires (6ème -> 5ème)
        self.niveau_5 = NiveauScolaire(
            code="5EME_TEST",
            nom="5ème",
            cycle="college",
            ordre=2,
        )
        db.session.add(self.niveau_5)
        db.session.flush()

        self.niveau_6 = NiveauScolaire(
            code="6EME_TEST",
            nom="6ème",
            cycle="college",
            ordre=1,
            niveau_suivant_id=self.niveau_5.id,
        )
        db.session.add(self.niveau_6)
        db.session.flush()

        # 4. Années scolaires : Source (active) et Cible (planifiée)
        self.annee_source = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2024-2025",
            date_debut=date(2024, 10, 1),
            date_fin=date(2025, 6, 30),
            statut="active",
        )
        self.annee_cible = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2025-2026",
            date_debut=date(2025, 10, 1),
            date_fin=date(2026, 6, 30),
            statut="planifiee",
        )
        db.session.add_all([self.annee_source, self.annee_cible])
        db.session.flush()

        # 5. Périodes officielles de l'année source (2 semestres)
        self.p_s1 = PeriodeBulletin(
            ecole_id=self.ecole.id,
            annee_id=self.annee_source.id,
            nom="Semestre 1",
            date_debut=date(2024, 10, 1),
            date_fin=date(2025, 1, 31),
            periode_active=False,
            publie=True,
        )
        self.p_s2 = PeriodeBulletin(
            ecole_id=self.ecole.id,
            annee_id=self.annee_source.id,
            nom="Semestre 2",
            date_debut=date(2025, 2, 1),
            date_fin=date(2025, 6, 30),
            periode_active=True,
            publie=True,
        )
        db.session.add_all([self.p_s1, self.p_s2])

        # 6. Classes
        self.classe_6a = Classe(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            niveau_id=self.niveau_6.id,
            nom="6ème A",
            statut="actif",
        )
        self.classe_5a = Classe(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            niveau_id=self.niveau_5.id,
            nom="5ème A",
            statut="actif",
        )
        db.session.add_all([self.classe_6a, self.classe_5a])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_deliberation_moyenne_annuelle_cursus_incomplet(self):
        """
        Règle 1:
        Un élève ayant 16/20 au Semestre 1 mais 0 note / absent au Semestre 2
        doit obtenir une moyenne divisée par 2 (8.0/20), être marqué 'Dossier Incomplet',
        et voir sa suggestion de passage bloquée au profit de 'Décision réservée au conseil (cursus incomplet)'.
        """
        eleve = Eleve(
            ecole_id=self.ecole.id,
            nom="Moussa",
            prenom="Aminata",
            matricule="24-0001",
            date_naissance=date(2012, 5, 10),
            statut="actif",
        )
        db.session.add(eleve)
        db.session.flush()

        insc = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            statut="inscrit",
        )
        db.session.add(insc)
        db.session.flush()

        # Bulletin S1 uniquement (moyenne 16.0)
        bulletin_s1 = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            inscription_id=insc.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            periode="Semestre 1",
            moyenne_generale=16.0,
            statut="valide",
        )
        db.session.add(bulletin_s1)
        db.session.commit()

        # Évaluation individuelle
        delib = evaluer_deliberation_annuelle(self.ecole.id, self.annee_source.id, eleve.id)
        self.assertTrue(delib["cursus_incomplet"])
        self.assertEqual(delib["statut_deliberation"], "Dossier Incomplet")
        self.assertEqual(delib["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(delib["nb_periodes_evaluees"], 1)
        self.assertEqual(delib["nb_periodes_attendues"], 2)
        # Moyenne réglementaire divisée par 2 périodes attendues: 16 / 2 = 8.0
        self.assertEqual(delib["moyenne"], 8.0)

        # Helper service bulletins_annuels
        res_regl = calculer_moyenne_annuelle_reglementaire(insc.id, self.ecole.id)
        self.assertTrue(res_regl["cursus_incomplet"])
        self.assertEqual(res_regl["statut"], "Dossier Incomplet")
        self.assertEqual(res_regl["suggestion"], "Décision réservée au conseil (cursus incomplet)")
        self.assertEqual(res_regl["moyenne_annuelle"], 8.0)

        # Évaluation globale par promotion
        delibs_all = get_deliberations_annuelles_eleves(self.ecole.id, self.annee_source.id)
        self.assertIn(eleve.id, delibs_all)
        self.assertTrue(delibs_all[eleve.id]["cursus_incomplet"])
        self.assertEqual(delibs_all[eleve.id]["suggestion"], "Décision réservée au conseil (cursus incomplet)")

    def test_deliberation_moyenne_annuelle_cursus_complet(self):
        """
        Règle 1 (suite):
        Un élève ayant les 2 semestres complétés (ex. 14.0 et 12.0)
        obtient moyenne 13.0, 'Complet' et suggestion 'passage'.
        """
        eleve = Eleve(
            ecole_id=self.ecole.id,
            nom="Diallo",
            prenom="Ousmane",
            matricule="24-0002",
            date_naissance=date(2012, 3, 15),
            statut="actif",
        )
        db.session.add(eleve)
        db.session.flush()

        insc = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            statut="inscrit",
        )
        db.session.add(insc)
        db.session.flush()

        b1 = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            inscription_id=insc.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            periode="Semestre 1",
            moyenne_generale=14.0,
            statut="valide",
        )
        b2 = Bulletin(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            inscription_id=insc.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            periode="Semestre 2",
            moyenne_generale=12.0,
            statut="valide",
        )
        db.session.add_all([b1, b2])
        db.session.commit()

        delib = evaluer_deliberation_annuelle(self.ecole.id, self.annee_source.id, eleve.id)
        self.assertFalse(delib["cursus_incomplet"])
        self.assertEqual(delib["statut_deliberation"], "Admis")
        self.assertEqual(delib["suggestion"], "passage")
        self.assertEqual(delib["moyenne"], 13.0)

    def test_transfert_eleve_avec_preinscription_future_existante(self):
        """
        Règle 2:
        Transfert / Sortie : lorsqu'une préinscription future N+1 existe déjà,
        l'action de transfert ne doit plus échouer avec l'erreur bloquante 'déjà traité'.
        La préinscription N+1 doit être automatiquement annulée ('annulee') et
        l'inscription source mise à jour en 'transfere' avec sa date et destination.
        """
        eleve = Eleve(
            ecole_id=self.ecole.id,
            nom="Traoré",
            prenom="Fatima",
            matricule="24-0003",
            date_naissance=date(2012, 8, 20),
            statut="actif",
        )
        db.session.add(eleve)
        db.session.flush()

        insc_source = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            statut="inscrit",
        )
        db.session.add(insc_source)
        db.session.flush()

        # Préinscription future N+1 préexistante
        insc_cible = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=eleve.id,
            classe_id=self.classe_5a.id,
            statut="preinscrit",
        )
        db.session.add(insc_cible)
        db.session.commit()

        # Exécution du transfert via executer_passage_eleve
        res, err = executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="transfert",
            classe_cible_id=None,
            motif_sortie="Déménagement familial à Maradi",
            etablissement_destination="Lycée Dan Baskoré",
            date_depart=date(2025, 6, 20),
        )
        self.assertIsNone(err, f"Le transfert ne doit pas renvoyer d'erreur: {err}")
        self.assertTrue(res["succes"])
        self.assertFalse(res.get("deja_traite", False))

        # Vérifications en base
        db.session.refresh(insc_source)
        db.session.refresh(insc_cible)
        db.session.refresh(eleve)

        self.assertEqual(insc_source.statut, "transfere")
        self.assertEqual(insc_source.decision_fin_annee, "transfert")
        self.assertEqual(insc_source.date_depart, date(2025, 6, 20))
        self.assertEqual(insc_source.etablissement_destination, "Lycée Dan Baskoré")

        # La préinscription N+1 est annulée
        self.assertEqual(insc_cible.statut, "annulee")
        self.assertEqual(eleve.statut, "transfere")

    def test_sortie_radiation_directe_via_service(self):
        """
        Règle 2 (suite):
        Test du helper direct transferer_ou_radier_eleve pour radiation/sortie.
        """
        eleve = Eleve(
            ecole_id=self.ecole.id,
            nom="Sani",
            prenom="Ibrahim",
            matricule="24-0004",
            date_naissance=date(2012, 11, 2),
            statut="actif",
        )
        db.session.add(eleve)
        db.session.flush()

        insc_source = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            statut="inscrit",
        )
        insc_cible = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            eleve_id=eleve.id,
            classe_id=self.classe_5a.id,
            statut="preinscrit",
        )
        db.session.add_all([insc_source, insc_cible])
        db.session.commit()

        ok, msg = transferer_ou_radier_eleve(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_source_id=self.annee_source.id,
            decision="radie",
            date_depart=date(2025, 5, 10),
            motif_sortie="Abandon scolaire",
        )
        self.assertTrue(ok)
        db.session.refresh(insc_source)
        db.session.refresh(insc_cible)
        db.session.refresh(eleve)

        self.assertEqual(insc_source.statut, "radie")
        self.assertEqual(insc_cible.statut, "annulee")
        self.assertEqual(eleve.statut, "radie")

    def test_propagation_frais_et_remises_synchronisation(self):
        """
        Règle 3:
        Propagation des frais & Remises : synchronisation instantanée de la modification
        des frais ou de la remise sur l'Inscription active et calcul financier harmonisé.
        """
        eleve = Eleve(
            ecole_id=self.ecole.id,
            nom="Abdou",
            prenom="Mariam",
            matricule="24-0005",
            date_naissance=date(2012, 7, 7),
            statut="actif",
            frais_annuels=80000.0,
        )
        db.session.add(eleve)
        db.session.flush()

        insc = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            statut="inscrit",
            frais_scolarite=80000.0,
            remise=5000.0,
            frais_inscription=10000.0,
        )
        db.session.add(insc)
        db.session.commit()

        # Synthèse initiale: Net = (80000 - 5000) + 10000 = 85000 FCFA
        syn_init = obtenir_synthese_financiere_eleve(self.ecole.id, eleve.id, self.annee_source.id)
        self.assertEqual(syn_init["montant_net"], 85000.0)
        self.assertEqual(syn_init["solde_restant"], 85000.0)

        # Modification des tarifs (Frais passent à 90 000, remise passe à 15 000)
        eleve.frais_annuels = 90000.0
        synchroniser_frais_et_remises_inscription(insc, nouveau_frais=90000.0, nouvelle_remise=15000.0)
        db.session.commit()

        # Vérification sur l'objet Inscription
        db.session.refresh(insc)
        self.assertEqual(insc.frais_scolarite, 90000.0)
        self.assertEqual(insc.remise, 15000.0)

        # Nouvelle synthèse financière: Net = (90000 - 15000) + 10000 = 85000 FCFA
        syn_new = obtenir_synthese_financiere_eleve(self.ecole.id, eleve.id, self.annee_source.id)
        self.assertEqual(syn_new["frais_scolarite"], 90000.0)
        self.assertEqual(syn_new["remise"], 15000.0)
        self.assertEqual(syn_new["montant_net"], 85000.0)

    def test_algorithme_relance_echeancier_anti_microversements(self):
        """
        Règle 4:
        Algorithme de relance intelligent :
        Année scolaire d'octobre à juin (9 mois).
        Frais nets = 90 000 FCFA (soit 10 000 FCFA / mois exigible).
        À la date du 15 décembre (3 mois écoulés: Octobre, Novembre, Décembre),
        le montant cumulé exigible est de 30 000 FCFA.
        Si un micro-versement de 100 FCFA est enregistré avec mois='Octobre',
        le retard réel est de 29 900 FCFA (> seuil de tolérance 1000 FCFA).
        L'élève DOIT être détecté en retard et l'alerte de relance DOIT se déclencher.
        """
        eleve = Eleve(
            ecole_id=self.ecole.id,
            nom="Garba",
            prenom="Souleymane",
            matricule="24-0006",
            date_naissance=date(2012, 4, 18),
            statut="actif",
        )
        db.session.add(eleve)
        db.session.flush()

        insc = Inscription(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            eleve_id=eleve.id,
            classe_id=self.classe_6a.id,
            statut="inscrit",
            frais_scolarite=90000.0,
            remise=0.0,
            frais_inscription=0.0,
        )
        db.session.add(insc)
        db.session.flush()

        # Micro-versement de 100 FCFA
        paiement_micro = Paiement(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            inscription_id=insc.id,
            annee=2024,
            montant=100.0,
            date_paiement=datetime(2024, 10, 15, 10, 0),
            mois="Octobre",
            statut="valide",
            reference="REC-TEST-MICRO-100",
        )
        db.session.add(paiement_micro)
        db.session.commit()

        # Date de référence : 15 Décembre 2024 (3 mois entamés: Oct, Nov, Dec)
        date_ref = date(2024, 12, 15)
        res_retard = calculer_retard_echeancier_inscription(
            insc,
            self.annee_source,
            date_reference=date_ref,
            seuil_tolerance=1000.0,
        )

        self.assertTrue(res_retard["est_en_retard"])
        self.assertEqual(res_retard["nb_mois_ecoules"], 3)
        self.assertEqual(res_retard["montant_exigible_a_date"], 30000.0)
        self.assertEqual(res_retard["total_paye"], 100.0)
        self.assertEqual(res_retard["retard"], 29900.0)

        # Génération des alertes automatiques à cette date
        alertes = generer_alertes_automatiques(self.ecole.id, self.annee_source.id, date_reference=date_ref)
        alertes_retard = [a for a in alertes if a.get("source") == "Paiements" or "Retard" in str(a.get("titre"))]
        self.assertGreater(len(alertes_retard), 0, "Une alerte de retard doit être levée pour l'élève")
        self.assertIn("Souleymane", alertes_retard[0]["message"])
        self.assertTrue(
            "29,900" in alertes_retard[0]["message"] or "29 900" in alertes_retard[0]["message"],
            f"Montant du retard attendu dans le message: {alertes_retard[0]['message']}"
        )


if __name__ == "__main__":
    unittest.main()
