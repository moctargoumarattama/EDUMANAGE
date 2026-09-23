"""
Tests unitaires pour la Tranche 2 du Passage d'Année (KLASORA):
1. Extension des statuts d'inscription ('preinscrit' supporté et par défaut sur année planifiée).
2. Bascule automatique de 'preinscrit' vers 'inscrit' lors d'un règlement.
3. Réintégration d'un ancien élève (droit à l'erreur après archivage).
4. Affichage des badges et filtres d'annuaire (Tous / Confirmés / Préinscrits).
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    AnneeScolaire,
    Classe,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Paiement,
    STATUTS_INSCRIPTION,
    Utilisateur,
)
from app.services.inscriptions_annuelles import (
    STATUTS_INSCRIPTION as SERVICE_STATUTS,
    creer_inscription_annuelle,
    get_inscription,
)
from app.services.passage_annee import (
    executer_passage_eleve,
    reinscrire_ancien_eleve,
)
from app.services.paiements_annuels import enregistrer_paiement
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash


class StatutPreinscritTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-preinscrit-cycle"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestStatutPreinscritEtCycle(unittest.TestCase):
    def setUp(self):
        self.app = create_app(StatutPreinscritTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        db.create_all()

        # 1. Établissement
        self.ecole = Ecole(
            nom="Complexe Scolaire Test",
            adresse="Niamey",
            telephone="91000000",
            email="direction@complexe-test.ne",
            statut="actif",
            onboarding_complete=True,
        )
        db.session.add(self.ecole)
        db.session.flush()

        # 2. Utilisateur Admin
        self.admin = Utilisateur(
            nom="Directeur",
            prenom="Klasora",
            email="directeur@complexe-test.ne",
            mot_de_passe=generate_password_hash("password123"),
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.admin)

        # 3. Année Source (Active) et Année Cible (Planifiée)
        self.annee_source = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
        )
        self.annee_cible = AnneeScolaire(
            ecole_id=self.ecole.id,
            nom="2026-2027",
            date_debut=date(2026, 9, 1),
            date_fin=date(2027, 6, 30),
            statut="planifiee",
        )
        db.session.add_all([self.annee_source, self.annee_cible])
        db.session.flush()

        # 4. Niveaux scolaires (CM2 -> 6ème)
        self.niveau_cm2 = NiveauScolaire(
            nom="CM2", code="CM2", ordre=5, cycle="primaire", ecole_id=self.ecole.id
        )
        self.niveau_6e = NiveauScolaire(
            nom="6ème", code="6E", ordre=6, cycle="college", ecole_id=self.ecole.id
        )
        db.session.add_all([self.niveau_cm2, self.niveau_6e])
        db.session.flush()

        self.niveau_cm2.niveau_suivant_id = self.niveau_6e.id

        # 5. Classes
        self.classe_source = Classe(
            nom="CM2 A",
            niveau="CM2",
            niveau_id=self.niveau_cm2.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_source.id,
            statut="ouverte",
        )
        self.classe_cible = Classe(
            nom="6ème A",
            niveau="6ème",
            niveau_id=self.niveau_6e.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )
        self.classe_source_cm2b = Classe(
            nom="CM2 B",
            niveau="CM2",
            niveau_id=self.niveau_cm2.id,
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee_cible.id,
            statut="ouverte",
        )
        db.session.add_all([self.classe_source, self.classe_cible, self.classe_source_cm2b])
        db.session.flush()

        # Activer le niveau 6e et CM2 dans la configuration annuelle cible
        from app.models import AnneeNiveauConfig
        cfg1 = AnneeNiveauConfig(ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id, niveau_id=self.niveau_6e.id, actif=True)
        cfg2 = AnneeNiveauConfig(ecole_id=self.ecole.id, annee_scolaire_id=self.annee_cible.id, niveau_id=self.niveau_cm2.id, actif=True)
        db.session.add_all([cfg1, cfg2])

        # 6. Élève
        self.eleve = Eleve(
            nom="Issa",
            prenom="Fatima",
            genre="F",
            date_naissance=date(2014, 5, 10),
            ecole_id=self.ecole.id,
            classe_id=self.classe_source.id,
            frais_annuels=150000.0,
            statut="actif",
        )
        db.session.add(self.eleve)
        db.session.flush()

        # Inscription source
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

    def test_01_constantes_statuts_contiennent_preinscrit(self):
        """Vérifie que 'preinscrit' fait partie des ensembles autorisés."""
        self.assertIn("preinscrit", STATUTS_INSCRIPTION)
        self.assertIn("preinscrit", SERVICE_STATUTS)
        self.assertIn("preinscrit", Inscription.STATUTS)

    def test_02_creation_inscription_annee_planifiee_defaut_preinscrit(self):
        """Une inscription créée pour une année planifiée reçoit par défaut le statut 'preinscrit'."""
        autre_eleve = Eleve(
            nom="Moussa",
            prenom="Ali",
            genre="M",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(autre_eleve)
        db.session.flush()

        insc, err = creer_inscription_annuelle(
            ecole_id=self.ecole.id,
            eleve_id=autre_eleve.id,
            annee_scolaire_id=self.annee_cible.id,
            classe_id=self.classe_cible.id,
            # statut non fourni -> doit défaut à preinscrit pour année planifiée
        )
        self.assertIsNone(err)
        self.assertIsNotNone(insc)
        self.assertEqual(insc.statut, "preinscrit")

    def test_03_passage_vers_annee_planifiee_donne_preinscrit(self):
        """L'exécution du passage vers l'année planifiée préinscrit l'élève."""
        res, err = executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.classe_cible.id,
        )
        self.assertIsNone(err)
        self.assertTrue(res["ok"])
        db.session.commit()

        insc_cible = Inscription.query.filter_by(
            eleve_id=self.eleve.id, annee_scolaire_id=self.annee_cible.id
        ).first()
        self.assertIsNotNone(insc_cible)
        self.assertEqual(insc_cible.statut, "preinscrit")

    def test_04_conversion_automatique_par_reglement(self):
        """Lorsqu'un acompte ou règlement est enregistré pour un préinscrit, son statut devient 'inscrit'."""
        # 1. On préinscrit l'élève pour l'année source (qui est active)
        autre_eleve = Eleve(
            nom="Bello",
            prenom="Amina",
            genre="F",
            ecole_id=self.ecole.id,
            frais_annuels=100000.0,
            statut="actif",
        )
        db.session.add(autre_eleve)
        db.session.flush()

        insc_pre, err = creer_inscription_annuelle(
            ecole_id=self.ecole.id,
            eleve_id=autre_eleve.id,
            annee_scolaire_id=self.annee_source.id,
            classe_id=self.classe_source.id,
            statut="preinscrit",
            frais_annuels=100000.0,
            sync_active=False,
        )
        self.assertIsNone(err)
        self.assertEqual(insc_pre.statut, "preinscrit")
        db.session.commit()

        # 2. Enregistrement d'un paiement partiel de 25 000 FCFA
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole.id,
            annee=self.annee_source,
            user=self.admin,
            eleve_id=autre_eleve.id,
            montant=25000.0,
            mois="Septembre",
            annee_civile=2025,
            mode_paiement="espèces",
        )
        self.assertIsNone(err)
        self.assertIsNotNone(paiement)
        self.assertTrue(getattr(paiement, "inscription_confirmee", False))
        db.session.commit()

        # 3. Vérification du statut de l'inscription
        db.session.refresh(insc_pre)
        db.session.refresh(autre_eleve)
        self.assertEqual(insc_pre.statut, "inscrit")
        self.assertEqual(autre_eleve.classe_id, self.classe_source.id)

    def test_05_paiement_sur_eleve_deja_inscrit_ne_declenche_pas_confirmation_superflue(self):
        """Un paiement pour un élève déjà 'inscrit' ne déclenche pas inscription_confirmee=True."""
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole.id,
            annee=self.annee_source,
            user=self.admin,
            eleve_id=self.eleve.id,
            montant=30000.0,
            mois="Octobre",
            annee_civile=2025,
            mode_paiement="espèces",
        )
        self.assertIsNone(err)
        self.assertFalse(getattr(paiement, "inscription_confirmee", False))

    def test_06_droit_a_lerreur_reinscription_apres_archivage(self):
        """Un élève non réinscrit ou revenant en cours d'année peut être réinscrit directement dans l'année active."""
        # On archive l'année source et on active l'année cible
        self.annee_source.statut = "archivee"
        self.annee_cible.statut = "active"
        db.session.commit()

        # Nouvel élève ancien sans inscription dans l'année active
        ancien_eleve = Eleve(
            nom="Garba",
            prenom="Oumarou",
            genre="M",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(ancien_eleve)
        db.session.flush()

        # Inscription historique dans l'année archivée
        insc_ancienne = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=ancien_eleve.id,
            annee_scolaire_id=self.annee_source.id,
            classe_id=self.classe_source.id,
            statut="termine",
            date_inscription=datetime.utcnow(),
        )
        db.session.add(insc_ancienne)
        db.session.commit()

        # Réinscription directe dans l'année active (6ème A)
        nouvelle_insc, err = reinscrire_ancien_eleve(
            eleve_id=ancien_eleve.id,
            annee_active_id=self.annee_cible.id,
            classe_cible_id=self.classe_cible.id,
            ecole_id=self.ecole.id,
            statut="inscrit",
        )
        self.assertIsNone(err)
        self.assertIsNotNone(nouvelle_insc)
        db.session.commit()

        # Vérifications
        db.session.refresh(ancien_eleve)
        self.assertEqual(nouvelle_insc.statut, "inscrit")
        self.assertEqual(nouvelle_insc.annee_scolaire_id, self.annee_cible.id)
        self.assertEqual(nouvelle_insc.classe_id, self.classe_cible.id)
        self.assertEqual(ancien_eleve.classe_id, self.classe_cible.id)

    def test_07_route_reinscription_ancien_eleve_post(self):
        """La route POST /annees/<id>/reinscrire_eleve permet au directeur de réintégrer un élève."""
        self._login_admin()

        eleve_retour = Eleve(
            nom="Diallo",
            prenom="Mariam",
            genre="F",
            ecole_id=self.ecole.id,
            statut="actif",
        )
        db.session.add(eleve_retour)
        db.session.commit()

        resp = self.client.post(
            f"/annees/{self.annee_source.id}/reinscrire_eleve",
            data={
                "eleve_id": eleve_retour.id,
                "classe_cible_id": self.classe_source.id,
                "statut": "inscrit",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

        insc = Inscription.query.filter_by(
            eleve_id=eleve_retour.id, annee_scolaire_id=self.annee_source.id
        ).first()
        self.assertIsNotNone(insc)
        self.assertEqual(insc.statut, "inscrit")
        self.assertEqual(insc.classe_id, self.classe_source.id)

    def test_08_ui_passage_annee_badge_preinscrit(self):
        """L'écran de passage d'année affiche le badge Préinscrit pour les inscriptions cibles préinscrites."""
        self._login_admin()

        # Exécuter passage vers annee_cible
        executer_passage_eleve(
            ecole_id=self.ecole.id,
            eleve_id=self.eleve.id,
            annee_source_id=self.annee_source.id,
            annee_cible_id=self.annee_cible.id,
            decision="passage",
            classe_cible_id=self.classe_cible.id,
        )
        db.session.commit()

        resp = self.client.get(f"/annees/{self.annee_source.id}/passage/{self.annee_cible.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Préinscrit (En attente d&#39;acompte)", html)

    def test_09_ui_eleves_onglets_statut_et_badge(self):
        """L'annuaire des élèves propose les onglets Tous / Confirmés / Préinscrits et le badge."""
        self._login_admin()

        resp = self.client.get("/eleves")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("Tous les élèves", html)
        self.assertIn("Confirmés (Inscrits)", html)
        self.assertIn("Préinscrits (En attente d&#39;acompte)", html)


if __name__ == "__main__":
    unittest.main()

