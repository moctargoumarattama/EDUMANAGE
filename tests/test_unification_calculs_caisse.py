"""Vérifie que la caisse utilise partout le montant net annuel."""

from datetime import date, datetime
import unittest

from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.config import Config
from app.models import AnneeScolaire, Classe, Ecole, Eleve, Inscription, Paiement, Utilisateur
from app.services.paiements_annuels import (
    get_finances_inscription,
    obtenir_synthese_financiere_eleve,
)


class CaisseUnifieeTestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-caisse-unifiee"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestUnificationCalculsCaisse(unittest.TestCase):
    def setUp(self):
        self.app = create_app(CaisseUnifieeTestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.ecole = Ecole(nom="École caisse Niger", onboarding_complete=True, ville="Niamey")
        db.session.add(self.ecole)
        db.session.flush()
        self.annee = AnneeScolaire(
            nom="2025-2026", date_debut=date(2025, 9, 1), date_fin=date(2026, 6, 30),
            statut="active", ecole_id=self.ecole.id,
        )
        self.classe = Classe(
            nom="CM2 A", niveau="CM2", statut="ouverte", ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        self.admin = Utilisateur(
            nom="Admin", prenom="Caisse", email="caisse-test@example.ne", role="admin",
            ecole_id=self.ecole.id, statut="actif", mot_de_passe=generate_password_hash("Secret123!"),
        )
        db.session.add_all([self.annee, self.classe, self.admin])
        db.session.flush()

        self.profils = {}
        profils = (
            ("Solde remise", 100000, 20000, 5000, 85000),
            ("Partiel remise", 100000, 20000, 5000, 50000),
            ("Standard", 100000, 0, 0, 100000),
            ("Bourse totale", 100000, 100000, 0, 0),
        )
        for index, (nom, base, remise, frais_inscription, versement) in enumerate(profils, 1):
            eleve = Eleve(
                nom=nom, prenom="Test", date_naissance=date(2014, 1, 1),
                ecole_id=self.ecole.id, code_parent=f"CAISSE{index:03d}", matricule=f"25-{index:04d}",
            )
            db.session.add(eleve)
            db.session.flush()
            inscription = Inscription(
                ecole_id=self.ecole.id, eleve_id=eleve.id, classe_id=self.classe.id,
                annee_scolaire_id=self.annee.id, statut="inscrit", frais_scolarite=base,
                frais_annuels=base, remise=remise, frais_inscription=frais_inscription,
            )
            db.session.add(inscription)
            db.session.flush()
            if versement:
                db.session.add(Paiement(
                    ecole_id=self.ecole.id, eleve_id=eleve.id, inscription_id=inscription.id,
                    montant=versement, mois="Octobre", annee=2025, mode_paiement="especes",
                    statut="payé", reference=f"CAISSE-{index}", date_paiement=datetime.utcnow(),
                ))
            self.profils[nom] = (eleve, inscription)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _login(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["ecole_id"] = self.ecole.id
            session["annee_id"] = self.annee.id
            session["onboarding_complete"] = True

    def test_formule_canonique_et_cas_limites(self):
        solde, inscription = self.profils["Solde remise"]
        finances = get_finances_inscription(inscription)
        self.assertEqual(finances["montant_net"], 85000.0)
        self.assertEqual(finances["total_paye"], 85000.0)
        self.assertEqual(finances["solde_restant"], 0.0)
        self.assertEqual(finances["statut_solde"], "complet")

        _, partiel = self.profils["Partiel remise"]
        self.assertEqual(get_finances_inscription(partiel)["solde_restant"], 35000.0)

        _, standard = self.profils["Standard"]
        self.assertEqual(get_finances_inscription(standard)["montant_net"], 100000.0)
        self.assertEqual(get_finances_inscription(standard)["solde_restant"], 0.0)

        eleve_bourse, _ = self.profils["Bourse totale"]
        synthese = obtenir_synthese_financiere_eleve(eleve_bourse.id, self.annee.id)
        self.assertEqual(synthese["montant_net"], 0.0)
        self.assertEqual(synthese["statut_solde"], "complet")

    def test_listing_ajax_et_filtres_utilisent_le_solde_net(self):
        self._login()
        response = self.client.get("/paiements?ajax=1")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        lignes = {ligne["eleve_nom"].split(maxsplit=1)[1]: ligne for ligne in data["inscriptions"]}
        self.assertEqual(lignes["Solde remise"]["reste_a_payer"], 0.0)
        self.assertEqual(lignes["Solde remise"]["statut_solde"], "complet")
        self.assertEqual(lignes["Partiel remise"]["reste_a_payer"], 35000.0)
        self.assertEqual(lignes["Standard"]["reste_a_payer"], 0.0)
        self.assertEqual(lignes["Bourse totale"]["reste_a_payer"], 0.0)

        response = self.client.get("/paiements?ajax=1&statut_solde=impaye")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [ligne["eleve_nom"].split(maxsplit=1)[1] for ligne in response.get_json()["inscriptions"]],
            ["Partiel remise"],
        )

        response = self.client.get("/paiements?ajax=1&statut_solde=complet")
        self.assertEqual(response.status_code, 200)
        soldes = {ligne["eleve_nom"].split(maxsplit=1)[1] for ligne in response.get_json()["inscriptions"]}
        self.assertEqual(soldes, {"Solde remise", "Standard", "Bourse totale"})

        response = self.client.get("/paiements?ajax=1&statut_solde=aucun")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [ligne["eleve_nom"].split(maxsplit=1)[1] for ligne in response.get_json()["inscriptions"]],
            ["Bourse totale"],
        )

    def test_detail_api_reutilise_le_service_canonique(self):
        self._login()
        _, inscription = self.profils["Solde remise"]
        response = self.client.get(f"/paiements/inscription/{inscription.id}/details")
        self.assertEqual(response.status_code, 200)
        resume = response.get_json()["resume"]
        self.assertEqual(resume["frais_annuels"], 85000.0)
        self.assertEqual(resume["remise"], 20000.0)
        self.assertEqual(resume["frais_inscription"], 5000.0)
        self.assertEqual(resume["reste_a_payer"], 0.0)

