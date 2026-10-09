"""Tests pour CHANTIER 3 :
1. Idempotence de caisse et de paie (anti-doublons & garde-fou 30s)
2. Scolarité à 0 FCFA pour boursiers / cas sociaux (InputRequired, zéro dette fantôme)
3. Rigueur et intégrité des certificats administratifs (scolarité active, radiation atomique, gel à l'émission)
"""
import io
import os
import unittest
from datetime import date, datetime, timedelta
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.config import Config
from app.models import (
    Utilisateur, Ecole, AnneeScolaire, Classe, Eleve, Inscription,
    Paiement, Professeur, FichePaiePersonnel, CertificatAdministratif
)
from app.services.paiements_annuels import (
    enregistrer_paiement,
    obtenir_synthese_financiere_eleve,
    get_finances_inscription
)
from app.forms import EleveForm


class Chantier3TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-chantier3"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestCaisseIdempotenceEtCertificatsStricts(unittest.TestCase):
    def setUp(self):
        self.app = create_app(Chantier3TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Établissement
        self.ecole = Ecole(nom="Lycée d'Excellence Niamey", onboarding_complete=True, ville="Niamey")
        db.session.add(self.ecole)
        db.session.commit()

        # Année scolaire active
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id
        )
        db.session.add(self.annee)
        db.session.commit()

        # Classe
        self.classe = Classe(
            nom="Tle D1",
            niveau="Terminale",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id
        )
        db.session.add(self.classe)
        db.session.commit()

        # Utilisateur Admin
        self.user_admin = Utilisateur(
            nom="Abdou",
            prenom="Moussa",
            email="directeur@excellence.ne",
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Admin1234!")
        )
        db.session.add(self.user_admin)
        db.session.commit()

        # Enseignant & Compte prof pour les tests de paie
        self.user_prof = Utilisateur(
            nom="Issa",
            prenom="Salifou",
            email="issa@excellence.ne",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof)
        db.session.commit()

        self.prof = Professeur(
            nom="Issa",
            prenom="Salifou",
            email="issa@excellence.ne",
            telephone="+22790998877",
            utilisateur_id=self.user_prof.id,
            ecole_id=self.ecole.id,
            type_remuneration="fixe",
            salaire_base=200000.0
        )
        db.session.add(self.prof)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_admin.id)
            sess['ecole_id'] = self.ecole.id

    def test_01_idempotence_paiement_eleve_avec_cle(self):
        """Vérifie que l'envoi répété avec la même idempotency_key renvoie le paiement existant sans doublon."""
        self._login_admin()

        eleve = Eleve(
            nom="Boureima",
            prenom="Nafissa",
            matricule="26-0010",
            genre="F",
            date_naissance=date(2008, 3, 12),
            ecole_id=self.ecole.id,
            frais_annuels=180000.0,
            contact_parent="+22790112233"
        )
        db.session.add(eleve)
        db.session.commit()

        inscription = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_scolaire_id=self.annee.id,
            classe_id=self.classe.id,
            statut="inscrit",
            frais_annuels=180000.0
        )
        db.session.add(inscription)
        db.session.commit()

        cle_idempotence = "pay-key-uuid-test-001"

        # 1er versement
        resp1 = self.client.post("/paiements", json={
            "eleve_id": eleve.id,
            "montant": 60000,
            "mois": "Octobre",
            "annee": 2025,
            "mode_paiement": "airtel_money",
            "reference": "AIR-101",
            "idempotency_key": cle_idempotence
        })
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.get_json()
        self.assertTrue(data1["success"])
        paiement_id_1 = data1["paiement_id"]
        self.assertFalse(data1.get("deja_traite", False))

        # 2ème versement identique avec la même clé (ex: double-clic ou rejeu réseau)
        resp2 = self.client.post("/paiements", json={
            "eleve_id": eleve.id,
            "montant": 60000,
            "mois": "Octobre",
            "annee": 2025,
            "mode_paiement": "airtel_money",
            "reference": "AIR-101",
            "idempotency_key": cle_idempotence
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertTrue(data2["success"])
        self.assertEqual(data2["paiement_id"], paiement_id_1)
        self.assertTrue(data2.get("deja_traite", False))

        # Vérification en base : exactement 1 paiement
        total_paiements = Paiement.query.filter_by(eleve_id=eleve.id).count()
        self.assertEqual(total_paiements, 1)

    def test_02_anti_rejeu_paiement_immediat_sans_cle(self):
        """Vérifie le garde-fou anti-rejeu < 30 secondes pour le même élève, montant et mode."""
        self._login_admin()

        eleve = Eleve(
            nom="Adamou",
            prenom="Souleymane",
            matricule="26-0011",
            genre="M",
            date_naissance=date(2007, 7, 20),
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
            contact_parent="+22790223344"
        )
        db.session.add(eleve)
        db.session.commit()

        inscription = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_scolaire_id=self.annee.id,
            classe_id=self.classe.id,
            statut="inscrit",
            frais_annuels=150000.0
        )
        db.session.add(inscription)
        db.session.commit()

        # 1er versement sans clé explicite
        resp1 = self.client.post("/paiements", json={
            "eleve_id": eleve.id,
            "montant": 50000,
            "mois": "Novembre",
            "annee": 2025,
            "mode_paiement": "especes"
        })
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.get_json()
        self.assertTrue(data1["success"])

        # 2ème versement immédiat (dans la fenêtre des 30s) avec mêmes paramètres
        resp2 = self.client.post("/paiements", json={
            "eleve_id": eleve.id,
            "montant": 50000,
            "mois": "Novembre",
            "annee": 2025,
            "mode_paiement": "especes"
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertTrue(data2["success"])
        self.assertTrue(data2.get("deja_traite", False))

        # Un seul paiement doit figurer en caisse
        total_paiements = Paiement.query.filter_by(eleve_id=eleve.id).count()
        self.assertEqual(total_paiements, 1)

    def test_03_paie_personnel_idempotence_et_anti_rejeu(self):
        """Vérifie l'idempotence et le blocage de doublon sur l'encaissement de salaire du personnel."""
        self._login_admin()

        fiche = FichePaiePersonnel(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            professeur_id=self.prof.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025",
            salaire_net=200000.0,
            montant_paye=0.0,
            statut_paiement="en_attente"
        )
        db.session.add(fiche)
        db.session.commit()

        cle_paie = "paie-idemp-uuid-002"

        # 1er règlement de 80 000 FCFA
        resp1 = self.client.post("/paie-personnel/enregistrer-reglement", json={
            "fiche_id": fiche.id,
            "montant_verse": 80000,
            "mode_reglement": "al_izza",
            "reference_recu": "REC-AI-01",
            "idempotency_key": cle_paie
        })
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.get_json()
        self.assertTrue(data1["success"])

        db.session.refresh(fiche)
        self.assertEqual(fiche.montant_paye, 80000.0)

        # 2ème envoi identique avec la même clé d'idempotence
        resp2 = self.client.post("/paie-personnel/enregistrer-reglement", json={
            "fiche_id": fiche.id,
            "montant_verse": 80000,
            "mode_reglement": "al_izza",
            "reference_recu": "REC-AI-01",
            "idempotency_key": cle_paie
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertTrue(data2["success"])
        self.assertTrue(data2.get("deja_traite", False))

        db.session.refresh(fiche)
        # Le salaire payé ne doit PAS avoir été doublé à 160 000 FCFA
        self.assertEqual(fiche.montant_paye, 80000.0)

        # 3ème envoi immédiat sans clé (anti-rejeu 30s)
        resp3 = self.client.post("/paie-personnel/enregistrer-reglement", json={
            "fiche_id": fiche.id,
            "montant_verse": 80000,
            "mode_reglement": "al_izza",
            "reference_recu": "REC-AI-01"
        })
        self.assertEqual(resp3.status_code, 200)
        data3 = resp3.get_json()
        self.assertTrue(data3.get("deja_traite", False))
        db.session.refresh(fiche)
        self.assertEqual(fiche.montant_paye, 80000.0)

    def test_04_scolarite_zero_fcfa_boursier_cas_social(self):
        """Vérifie qu'un élève boursier à 0 FCFA est validé, sans dette fantôme de 150 000 FCFA."""
        self._login_admin()

        # Création élève avec frais = 0.0
        eleve = Eleve(
            nom="Hassane",
            prenom="Fatima",
            matricule="26-0012",
            genre="F",
            date_naissance=date(2009, 1, 15),
            ecole_id=self.ecole.id,
            frais_annuels=0.0,
            contact_parent="+22790334455"
        )
        db.session.add(eleve)
        db.session.commit()

        inscription = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_scolaire_id=self.annee.id,
            classe_id=self.classe.id,
            statut="inscrit",
            frais_annuels=0.0
        )
        db.session.add(inscription)
        db.session.commit()

        # Synthèse financière
        synthese = obtenir_synthese_financiere_eleve(eleve.id, self.annee.id)
        self.assertEqual(synthese["frais_du"], 0.0)
        self.assertEqual(synthese["reste_a_payer"], 0.0)
        self.assertEqual(synthese["total_paye"], 0.0)
        self.assertEqual(synthese["statut_solde"], "complet")

        finances = get_finances_inscription(inscription)
        self.assertEqual(finances["frais_du"], 0.0)
        self.assertEqual(finances["reste_a_payer"], 0.0)
        self.assertEqual(finances["statut_solde"], "complet")

    def test_05_certificat_scolarite_conditionne_inscription_active(self):
        """Point 6 : Le certificat de scolarité doit être strictement refusé (400) si l'élève n'a pas d'inscription active."""
        self._login_admin()

        eleve = Eleve(
            nom="Sani",
            prenom="Mahamadou",
            matricule="26-0013",
            genre="M",
            date_naissance=date(2008, 6, 10),
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
            contact_parent="+22790445566"
        )
        db.session.add(eleve)
        db.session.commit()

        # Cas A : Aucune inscription du tout -> Refus 400
        resp_sans_insc = self.client.post("/certificats/generer", json={
            "eleve_id": eleve.id,
            "type_certificat": "scolarite"
        })
        self.assertEqual(resp_sans_insc.status_code, 400)
        data_err = resp_sans_insc.get_json()
        self.assertIn("inscription active", data_err["error"])

        # Cas B : Inscription radiée / annulée -> Refus 400
        inscription_radiee = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_scolaire_id=self.annee.id,
            classe_id=self.classe.id,
            statut="radie",
            frais_annuels=150000.0
        )
        db.session.add(inscription_radiee)
        db.session.commit()

        resp_radie = self.client.post("/certificats/generer", json={
            "eleve_id": eleve.id,
            "type_certificat": "scolarite"
        })
        self.assertEqual(resp_radie.status_code, 400)

        # Cas C : Inscription active ("inscrit") -> Succès 200
        inscription_radiee.statut = "inscrit"
        db.session.commit()

        resp_ok = self.client.post("/certificats/generer", json={
            "eleve_id": eleve.id,
            "type_certificat": "scolarite"
        })
        self.assertEqual(resp_ok.status_code, 200)
        data_ok = resp_ok.get_json()
        self.assertTrue(data_ok["success"])
        self.assertTrue(data_ok["certificat"]["reference"].startswith("CS-"))

    def test_06_certificat_transfert_radiation_transition_atomique(self):
        """Point 7 : L'émission d'un certificat de transfert/radiation bascule formellement l'inscription à 'radie' / 'transfere'."""
        self._login_admin()

        eleve = Eleve(
            nom="Tahirou",
            prenom="Amina",
            matricule="26-0014",
            genre="F",
            date_naissance=date(2008, 9, 2),
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
            contact_parent="+22790556677"
        )
        db.session.add(eleve)
        db.session.commit()

        inscription = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_scolaire_id=self.annee.id,
            classe_id=self.classe.id,
            statut="inscrit",
            frais_annuels=150000.0
        )
        db.session.add(inscription)
        db.session.commit()

        # Émission du certificat de transfert / radiation
        resp = self.client.post("/certificats/generer", json={
            "eleve_id": eleve.id,
            "type_certificat": "radiation",
            "date_depart": "2026-04-10",
            "etablissement_destination": "Lycée Dan Baskoré Maradi"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["certificat"]["reference"].startswith("CR-"))

        # Vérification en base de données de la transition d'inscription
        db.session.refresh(inscription)
        db.session.refresh(eleve)
        self.assertEqual(inscription.statut, "radie")
        self.assertEqual(inscription.date_depart, date(2026, 4, 10))
        self.assertEqual(inscription.etablissement_destination, "Lycée Dan Baskoré Maradi")
        self.assertEqual(eleve.statut, "radie")

    def test_07_immutabilite_donnees_certificat_a_l_emission(self):
        """Point 8 : Le certificat gèle l'identité de l'élève au moment de l'émission et ne change pas en cas de modification ultérieure."""
        self._login_admin()

        eleve = Eleve(
            nom="Saley",
            prenom="Ibrahim",
            matricule="26-0015",
            genre="M",
            date_naissance=date(2008, 11, 25),
            lieu_naissance="Zinder",
            nationalite="Nigérienne",
            nom_pere="Saley Amadou",
            nom_mere="Hadiza Moussa",
            numero_acte="Acte 452/2008",
            ecole_id=self.ecole.id,
            frais_annuels=150000.0,
            contact_parent="+22790667788"
        )
        db.session.add(eleve)
        db.session.commit()

        inscription = Inscription(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            annee_scolaire_id=self.annee.id,
            classe_id=self.classe.id,
            statut="inscrit",
            frais_annuels=150000.0
        )
        db.session.add(inscription)
        db.session.commit()

        # Émission du certificat
        resp = self.client.post("/certificats/generer", json={
            "eleve_id": eleve.id,
            "type_certificat": "scolarite"
        })
        self.assertEqual(resp.status_code, 200)
        cert_data = resp.get_json()["certificat"]
        cert_id = cert_data["id"]

        cert = db.session.get(CertificatAdministratif, cert_id)
        self.assertEqual(cert.nom_eleve, "Saley")
        self.assertEqual(cert.prenom_eleve, "Ibrahim")
        self.assertEqual(cert.matricule_eleve, "26-0015")
        self.assertEqual(cert.lieu_naissance_eleve, "Zinder")
        self.assertEqual(cert.nom_pere_eleve, "Saley Amadou")

        # Modification ultérieure du profil de l'élève (changement nom, prénom, lieu de naissance)
        eleve.nom = "NomModifie"
        eleve.prenom = "PrenomModifie"
        eleve.lieu_naissance = "Agadez"
        db.session.commit()

        # Re-vérification du certificat : les données gelées restent intactes
        db.session.refresh(cert)
        self.assertEqual(cert.nom_eleve, "Saley")
        self.assertEqual(cert.prenom_eleve, "Ibrahim")
        self.assertEqual(cert.lieu_naissance_eleve, "Zinder")
        self.assertEqual(cert.matricule_eleve, "26-0015")

        # Test de rendu d'impression A4 : doit afficher les données historiques scellées
        resp_print = self.client.get(f"/certificats/imprimer/{cert.id}")
        self.assertEqual(resp_print.status_code, 200)
        html_content = resp_print.get_data(as_text=True)
        self.assertIn("SALEY", html_content)
        self.assertIn("Ibrahim", html_content)
        self.assertIn("26-0015", html_content)
        self.assertNotIn("NomModifie", html_content)

        # Test de vérification publique QR Code : affiche les données scellées
        resp_verif = self.client.get(f"/certificats/verifier/{cert.code_verification}")
        self.assertEqual(resp_verif.status_code, 200)
        verif_html = resp_verif.get_data(as_text=True)
        self.assertIn("Saley", verif_html)
        self.assertIn("Ibrahim", verif_html)


if __name__ == "__main__":
    unittest.main()
