import io
import os
import unittest
from datetime import date, datetime
import openpyxl
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash

from app import create_app, db
from app.config import Config
from app.models import (
    Utilisateur, Ecole, AnneeScolaire, Classe, Eleve, Inscription,
    Paiement, Professeur, PointagePersonnel, FichePaiePersonnel, CertificatAdministratif
)
from app.services.paiements_annuels import (
    MODES_PAIEMENT_NIGER,
    MODES_REGLEMENT_AUTORISES,
    normaliser_mode_paiement,
    enregistrer_paiement
)
from app.services.import_eleves_service import previsualiser_import_excel, executer_import_excel
from scripts.migrate_niger_pratique import migrate as run_niger_migration


class NigerPratiqueTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-niger"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestNigerPratique(unittest.TestCase):
    def setUp(self):
        self.app = create_app(NigerPratiqueTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Établissement
        self.ecole = Ecole(nom="Complexe Scolaire Sahel Niamey", onboarding_complete=True, ville="Niamey")
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
            nom="6ème A",
            niveau="6eme",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id
        )
        db.session.add(self.classe)
        db.session.commit()

        # Utilisateur Admin
        self.user_admin = Utilisateur(
            nom="Ousmane",
            prenom="Ibrahim",
            email="directeur@sahel.ne",
            role="admin",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Admin1234!")
        )
        db.session.add(self.user_admin)
        db.session.commit()

        # Utilisateur Enseignant
        self.user_prof = Utilisateur(
            nom="Garba",
            prenom="Idrissa",
            email="garba@sahel.ne",
            role="professeur",
            ecole_id=self.ecole.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Prof1234!")
        )
        db.session.add(self.user_prof)
        db.session.commit()

        # Enseignant / Personnel pour paie
        self.prof = Professeur(
            nom="Garba",
            prenom="Idrissa",
            email="garba@sahel.ne",
            telephone="+22790112233",
            utilisateur_id=self.user_prof.id,
            ecole_id=self.ecole.id,
            type_remuneration="fixe",
            salaire_base=150000.0
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

    def test_modes_catalogue_niger(self):
        """Vérifie que les modes autorisés contiennent exclusivement les canaux du Niger."""
        attendus = {'especes', 'airtel_money', 'moov_money', 'al_izza', 'nita', 'amana', 'virement', 'cheque'}
        self.assertEqual(MODES_REGLEMENT_AUTORISES, attendus)
        self.assertNotIn('wave', MODES_REGLEMENT_AUTORISES)
        self.assertNotIn('orange_money', MODES_REGLEMENT_AUTORISES)
        self.assertNotIn('mtn', MODES_REGLEMENT_AUTORISES)

        # Normalisation
        self.assertEqual(normaliser_mode_paiement('airtel_money'), 'airtel_money')
        self.assertEqual(normaliser_mode_paiement('Airtel Money'), 'airtel_money')
        self.assertEqual(normaliser_mode_paiement('moov_money'), 'moov_money')
        self.assertEqual(normaliser_mode_paiement('Flooz'), 'moov_money')
        self.assertEqual(normaliser_mode_paiement('al_izza'), 'al_izza')
        self.assertEqual(normaliser_mode_paiement('Al Izza Transfert'), 'al_izza')
        self.assertEqual(normaliser_mode_paiement('nita'), 'nita')
        self.assertEqual(normaliser_mode_paiement('amana'), 'amana')
        self.assertIsNone(normaliser_mode_paiement('wave'))
        self.assertIsNone(normaliser_mode_paiement('orange_money'))
        self.assertIsNone(normaliser_mode_paiement('mtn'))

    def test_paiements_autorises_niger(self):
        """Test 1: Enregistrement avec 'airtel_money', 'moov_money', 'al_izza', 'especes' -> HTTP 200."""
        self._login_admin()

        # Créer élève et inscription
        eleve = Eleve(
            nom="Abdou",
            prenom="Moussa",
            date_naissance=date(2012, 5, 10),
            ecole_id=self.ecole.id,
            annee_premiere_ecole=2025,
            matricule="25-0001",
            nationalite="Nigérienne"
        )
        db.session.add(eleve)
        db.session.commit()

        ins = Inscription(
            eleve_id=eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_scolarite=250000.0,
            statut="inscrit"
        )
        db.session.add(ins)
        db.session.commit()

        modes_a_tester = ['airtel_money', 'moov_money', 'al_izza', 'especes', 'nita', 'amana', 'virement', 'cheque']
        for idx, mode in enumerate(modes_a_tester):
            resp = self.client.post(
                '/paiements',
                json={
                    "eleve_id": eleve.id,
                    "montant": 10000 + (idx * 500),
                    "mois": "Octobre",
                    "annee": 2025,
                    "mode_paiement": mode,
                    "reference": f"TEST-{mode}-{idx}"
                },
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 200, f"Le mode {mode} aurait dû être accepté")
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("mode_paiement"), mode)

        # Vérifier en base
        total_paiements = Paiement.query.filter_by(eleve_id=eleve.id).count()
        self.assertEqual(total_paiements, len(modes_a_tester))

    def test_paiements_rejetes_non_niger(self):
        """Test 2: Tentative de paiement avec 'wave', 'orange_money', 'autre' -> HTTP 400 sans écriture."""
        self._login_admin()

        eleve = Eleve(
            nom="Harouna",
            prenom="Amina",
            date_naissance=date(2013, 8, 14),
            ecole_id=self.ecole.id,
            annee_premiere_ecole=2025,
            matricule="25-0002"
        )
        db.session.add(eleve)
        db.session.commit()

        ins = Inscription(
            eleve_id=eleve.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            frais_scolarite=200000.0,
            statut="inscrit"
        )
        db.session.add(ins)
        db.session.commit()

        modes_invalides = ['wave', 'orange_money', 'mtn', 'autre', 'inconnu', 'crypto']
        for mode in modes_invalides:
            resp = self.client.post(
                '/paiements',
                json={
                    "eleve_id": eleve.id,
                    "montant": 15000,
                    "mois": "Novembre",
                    "annee": 2025,
                    "mode_paiement": mode,
                },
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 400, f"Le mode invalide '{mode}' aurait dû être rejeté avec 400")
            data = resp.get_json()
            self.assertFalse(data.get("success"))
            self.assertIn("Mode de règlement invalide", data.get("error", ""))

        # Aucun paiement ne doit avoir été créé en base
        self.assertEqual(Paiement.query.filter_by(eleve_id=eleve.id).count(), 0)

        # Rejet direct par le service
        paiement, err = enregistrer_paiement(
            ecole_id=self.ecole.id,
            annee=self.annee,
            user=self.user_admin,
            eleve_id=eleve.id,
            montant=20000,
            mois="Décembre",
            annee_civile=2025,
            mode_paiement='wave'
        )
        self.assertIsNone(paiement)
        self.assertIn("Mode de règlement invalide", err)

    def test_paie_personnel_modes_niger(self):
        """Vérifie l'acceptation des modes Niger et le rejet de wave/orange_money sur la paie du personnel."""
        self._login_admin()

        # Créer une fiche de paie
        fiche = FichePaiePersonnel(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            professeur_id=self.prof.id,
            mois=10,
            annee=2025,
            periode_nom="Octobre 2025",
            type_remuneration="fixe",
            salaire_base=150000.0,
            salaire_net=150000.0,
            montant_paye=0.0,
            statut_paiement="en_attente"
        )
        db.session.add(fiche)
        db.session.commit()

        # Rejet d'un mode étranger (wave)
        resp_bad = self.client.post(
            '/paie-personnel/enregistrer-reglement',
            json={
                "fiche_id": fiche.id,
                "montant_verse": 50000,
                "mode_reglement": "wave"
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp_bad.status_code, 400)
        self.assertFalse(resp_bad.get_json().get("success"))

        # Succès avec Airtel Money
        resp_ok = self.client.post(
            '/paie-personnel/enregistrer-reglement',
            json={
                "fiche_id": fiche.id,
                "montant_verse": 50000,
                "mode_reglement": "airtel_money"
            },
            headers={"X-Requested-With": "XMLHttpRequest"}
        )
        self.assertEqual(resp_ok.status_code, 200)
        self.assertTrue(resp_ok.get_json().get("success"))

        db.session.refresh(fiche)
        self.assertEqual(fiche.montant_paye, 50000.0)
        self.assertEqual(fiche.mode_paiement, "airtel_money")

    def test_etat_civil_eleve_persistence_et_modification(self):
        """Test 3: Création et modification d'un élève avec nationalite, numero_acte, nom_pere, nom_mere."""
        eleve = Eleve(
            nom="Adamou",
            prenom="Souleymane",
            genre="M",
            date_naissance=date(2012, 3, 15),
            lieu_naissance="Niamey",
            ecole_id=self.ecole.id,
            annee_premiere_ecole=2025,
            matricule="25-0010",
            nationalite="Nigérienne",
            numero_acte="1234/2012",
            nom_pere="Amadou",
            nom_mere="Fatima"
        )
        db.session.add(eleve)
        db.session.commit()

        # Relecture en base
        eleve_db = db.session.get(Eleve, eleve.id)
        self.assertEqual(eleve_db.nationalite, "Nigérienne")
        self.assertEqual(eleve_db.numero_acte, "1234/2012")
        self.assertEqual(eleve_db.nom_pere, "Amadou")
        self.assertEqual(eleve_db.nom_mere, "Fatima")

        # Vérification to_dict()
        eleve_dict = eleve_db.to_dict()
        self.assertEqual(eleve_dict["nationalite"], "Nigérienne")
        self.assertEqual(eleve_dict["numero_acte"], "1234/2012")
        self.assertEqual(eleve_dict["nom_pere"], "Amadou")
        self.assertEqual(eleve_dict["nom_mere"], "Fatima")

        # Modification
        eleve_db.numero_acte = "5678/2012"
        eleve_db.nom_mere = "Aïchatou"
        db.session.commit()

        db.session.refresh(eleve_db)
        self.assertEqual(eleve_db.numero_acte, "5678/2012")
        self.assertEqual(eleve_db.nom_mere, "Aïchatou")

    def test_certificat_rendu_etat_civil_niger(self):
        """Test 4: Génération/rendu du certificat de scolarité avec nationalité et mentions de filiation."""
        self._login_admin()

        eleve = Eleve(
            nom="Tahirou",
            prenom="Balkissa",
            genre="F",
            date_naissance=date(2011, 7, 22),
            lieu_naissance="Maradi",
            ecole_id=self.ecole.id,
            annee_premiere_ecole=2025,
            matricule="25-0020",
            nationalite="Nigérienne",
            numero_acte="789/2011",
            nom_pere="Tahirou Oumarou",
            nom_mere="Hadiza Boubacar"
        )
        db.session.add(eleve)
        db.session.commit()

        cert = CertificatAdministratif(
            ecole_id=self.ecole.id,
            eleve_id=eleve.id,
            type_certificat="scolarite",
            reference="CS-2025-0001",
            annee_scolaire="2025-2026",
            classe_nom="6ème A",
            niveau="Collège",
            ville_emission="Niamey",
            date_emission=date.today(),
            signataire_nom="M. Ousmane Ibrahim",
            signataire_titre="Le Proviseur",
            code_verification="code-sec-test-cert-12345"
        )
        db.session.add(cert)
        db.session.commit()

        resp = self.client.get(f'/certificats/imprimer/{cert.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Vérifications des mentions obligatoires dans le certificat
        self.assertIn("Nigérienne", html)
        self.assertIn("Fille de", html)
        self.assertIn("Tahirou Oumarou", html)
        self.assertIn("Hadiza Boubacar", html)
        self.assertIn("789/2011", html)
        self.assertIn("25-0020", html)

    def test_import_excel_etat_civil_niger(self):
        """Vérifie que l'import Excel prend en compte les 4 champs d'état civil Niger."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Eleves"

        # Colonnes attendues par le modèle Klasora :
        # 0: Nom, 1: Prénom, 2: Genre, 3: Date de naissance, 4: Lieu de naissance,
        # 5: Adresse, 6: Nom tuteur, 7: Téléphone tuteur, 8: Email tuteur,
        # 9: Classe, 10: Statut, 11: Nationalité, 12: N° Acte/Jugement, 13: Nom du père, 14: Nom de la mère
        headers = [
            "Nom", "Prénom", "Genre (M/F)", "Date de naissance (JJ/MM/AAAA)",
            "Lieu de naissance", "Adresse", "Nom du parent / tuteur", "Téléphone parent", "Email parent",
            "Classe", "Statut",
            "Nationalité", "N° Acte de naissance / Jugement", "Nom du père", "Nom de la mère"
        ]
        ws.append(headers)

        ws.append([
            "Dan Baba", "Ibrahim", "M", "10/04/2012",
            "Tahoua", "Quartier Plateau", "Dan Baba Issoufou", "+22796123456", "danbaba@test.ne",
            "6ème A", "actif",
            "Nigérienne", "456/2012", "Dan Baba Issoufou", "Mariama Seydou"
        ])

        stream = io.BytesIO()
        wb.save(stream)
        stream.seek(0)

        # 1. Analyse / Prévisualisation
        prev_result = previsualiser_import_excel(stream, self.ecole.id, self.annee)
        self.assertTrue(prev_result.get("is_importable"))
        self.assertEqual(prev_result.get("valides"), 1)

        lignes = prev_result.get("lignes", [])
        self.assertEqual(len(lignes), 1)
        ligne_info = lignes[0]
        self.assertEqual(ligne_info.get("nationalite"), "Nigérienne")
        self.assertEqual(ligne_info.get("numero_acte"), "456/2012")
        self.assertEqual(ligne_info.get("nom_pere"), "Dan Baba Issoufou")
        self.assertEqual(ligne_info.get("nom_mere"), "Mariama Seydou")

        # 2. Exécution
        succes, message, nb_crees, nb_reinscrits, nb_ignores = executer_import_excel(lignes, self.ecole.id, self.annee)
        self.assertTrue(succes)
        self.assertEqual(nb_crees, 1)

        # 3. Vérification en base
        eleve_cree = Eleve.query.filter_by(ecole_id=self.ecole.id, nom="Dan Baba").first()
        self.assertIsNotNone(eleve_cree)
        self.assertEqual(eleve_cree.nationalite, "Nigérienne")
        self.assertEqual(eleve_cree.numero_acte, "456/2012")
        self.assertEqual(eleve_cree.nom_pere, "Dan Baba Issoufou")
        self.assertEqual(eleve_cree.nom_mere, "Mariama Seydou")

    def test_migration_idempotente(self):
        """Vérifie que le script de migration s'exécute de façon idempotente sans erreur."""
        # L'exécuter une première fois
        run_niger_migration(self.app)

        # Créer un élève sans nationalité explicite (simulate legacy)
        eleve = Eleve(
            nom="Test",
            prenom="Migration",
            date_naissance=date(2010, 1, 1),
            ecole_id=self.ecole.id,
            annee_premiere_ecole=2024,
            matricule="24-0099",
            nationalite=None
        )
        db.session.add(eleve)
        db.session.commit()

        # Ré-exécuter la migration : doit remplir 'Nigérienne' sans crash
        run_niger_migration(self.app)

        db.session.refresh(eleve)
        self.assertEqual(eleve.nationalite, "Nigérienne")


if __name__ == '__main__':
    unittest.main()
