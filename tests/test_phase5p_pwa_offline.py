"""
Tests KLASORA — Phase 5P BIS : Finalisation Offline Commerciale & PWA Sync.
Exactement 8 tests ciblés selon les exigences produit.
"""
from datetime import date, datetime
import unittest

from app import create_app, db
from app.models import (
    Absence,
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    Professeur,
    SyncOperationLog,
    Utilisateur,
    professeur_classes,
)
from sqlalchemy.pool import StaticPool


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False}
    }
    SECRET_KEY = "test-phase5p"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestPhase5POfflineCommercial(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # 1. École A & B
        self.ecole_a = Ecole(
            nom="École A de Niamey",
            adresse="Avenue du Fleuve",
            telephone="90000001",
            email="contact@ecole-a.ne",
            directeur="M. Mamane",
            ville="Niamey"
        )
        self.ecole_b = Ecole(
            nom="École B de Maradi",
            adresse="Quartier Zongo",
            telephone="90000002",
            email="contact@ecole-b.ne",
            directeur="Mme Aïcha",
            ville="Maradi"
        )
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()

        # 2. Années scolaires actives
        self.annee_a = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 15),
            date_fin=date(2025, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id
        )
        self.annee_b = AnneeScolaire(
            nom="2024-2025",
            date_debut=date(2024, 9, 15),
            date_fin=date(2025, 6, 30),
            statut="active",
            ecole_id=self.ecole_b.id
        )
        db.session.add_all([self.annee_a, self.annee_b])
        db.session.commit()

        # 3. Niveaux et Classes
        self.niveau_6e = NiveauScolaire(code="6E", nom="Sixième", cycle="college", ordre=1)
        db.session.add(self.niveau_6e)
        db.session.commit()

        self.config_a = AnneeNiveauConfig(
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a.id,
            niveau_id=self.niveau_6e.id,
            actif=True
        )
        self.config_b = AnneeNiveauConfig(
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_b.id,
            niveau_id=self.niveau_6e.id,
            actif=True
        )
        db.session.add_all([self.config_a, self.config_b])
        db.session.commit()

        self.classe_a = Classe(
            nom="6ème A",
            niveau="6E",
            ecole_id=self.ecole_a.id,
            annee_scolaire_id=self.annee_a.id
        )
        self.classe_b = Classe(
            nom="6ème B",
            niveau="6E",
            ecole_id=self.ecole_b.id,
            annee_scolaire_id=self.annee_b.id
        )
        db.session.add_all([self.classe_a, self.classe_b])
        db.session.commit()

        # 4. Utilisateurs
        self.admin_a = Utilisateur(
            nom="Admin A",
            prenom="Admin",
            email="admin@ecole-a.ne",
            mot_de_passe="pass123",
            role="admin",
            ecole_id=self.ecole_a.id
        )

        self.admin_b = Utilisateur(
            nom="Admin B",
            prenom="Admin",
            email="admin@ecole-b.ne",
            mot_de_passe="pass123",
            role="admin",
            ecole_id=self.ecole_b.id
        )

        self.parent_a = Utilisateur(
            nom="Parent A",
            prenom="Oumarou",
            email="parent@ecole-a.ne",
            mot_de_passe="pass123",
            role="parent",
            ecole_id=self.ecole_a.id
        )

        self.user_prof_a = Utilisateur(
            nom="Prof A",
            prenom="Moussa",
            email="prof@ecole-a.ne",
            mot_de_passe="pass123",
            role="professeur",
            ecole_id=self.ecole_a.id
        )
        db.session.add_all([self.admin_a, self.admin_b, self.parent_a, self.user_prof_a])
        db.session.commit()

        # 5. Professeur et Cours
        self.prof_a = Professeur(
            nom="Prof",
            prenom="Moussa",
            email="prof@ecole-a.ne",
            code_prof="PROF001",
            ecole_id=self.ecole_a.id,
            utilisateur_id=self.user_prof_a.id
        )
        db.session.add(self.prof_a)
        db.session.commit()

        db.session.execute(professeur_classes.insert().values(
            professeur_id=self.prof_a.id,
            classe_id=self.classe_a.id,
            ecole_id=self.ecole_a.id
        ))
        db.session.commit()

        self.cours_math = Cours(
            nom="Mathématiques",
            classe_id=self.classe_a.id,
            professeur_id=self.prof_a.id,
            ecole_id=self.ecole_a.id
        )
        db.session.add(self.cours_math)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, email):
        with self.client.session_transaction() as sess:
            u = Utilisateur.query.filter_by(email=email).first()
            sess['_user_id'] = str(u.id)
            sess['ecole_id'] = u.ecole_id
            sess['annee_consultee_id'] = self.annee_a.id if u.ecole_id == self.ecole_a.id else self.annee_b.id

    def _logout(self):
        with self.client.session_transaction() as sess:
            sess.clear()

    # 1. Création élève offline -> sync crée un seul élève
    def test_01_creation_eleve_offline_sync(self):
        self._login(self.admin_a.email)

        payload = {
            "operations": [
                {
                    "client_op_id": "op-eleve-001",
                    "type": "eleve_creation",
                    "local_student_uuid": "uuid-temp-001",
                    "nom": "Oumarou",
                    "prenom": "Ibrahim",
                    "date_naissance": "2012-05-10",
                    "genre": "M",
                    "frais_annuels": 150000.0,
                    "classe_id": self.classe_a.id
                }
            ]
        }

        resp = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["processed"], 1)

        result_item = data["results"][0]
        self.assertEqual(result_item["status"], "synced")
        self.assertEqual(result_item["local_student_uuid"], "uuid-temp-001")
        self.assertIsNotNone(result_item["entity_id"])

        # Vérification en base de données
        eleve = Eleve.query.filter_by(nom="Oumarou", prenom="Ibrahim", ecole_id=self.ecole_a.id).first()
        self.assertIsNotNone(eleve)
        self.assertEqual(eleve.id, result_item["entity_id"])

    # 2. Même client_op_id envoyé deux fois -> aucun doublon
    def test_02_idempotence_aucun_doublon(self):
        self._login(self.admin_a.email)

        payload = {
            "operations": [
                {
                    "client_op_id": "op-idempotent-002",
                    "type": "eleve_creation",
                    "local_student_uuid": "uuid-temp-002",
                    "nom": "Abdou",
                    "prenom": "Fatima",
                    "date_naissance": "2013-01-20",
                    "genre": "F"
                }
            ]
        }

        # Premier envoi
        resp1 = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.get_json()
        self.assertEqual(data1["results"][0]["status"], "synced")

        # Second envoi identique
        resp2 = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertEqual(data2["results"][0]["status"], "already_processed")

        # Vérification qu'il n'y a qu'un seul élève créé
        count = Eleve.query.filter_by(nom="Abdou", prenom="Fatima", ecole_id=self.ecole_a.id).count()
        self.assertEqual(count, 1)

    # 3. Élève offline + inscription -> ordre correct et IDs réconciliés
    def test_03_eleve_offline_et_inscription_reconciliee(self):
        self._login(self.admin_a.email)

        payload = {
            "operations": [
                {
                    "client_op_id": "op-eleve-003",
                    "type": "eleve_creation",
                    "local_student_uuid": "uuid-temp-003",
                    "nom": "Salifou",
                    "prenom": "Ali",
                    "date_naissance": "2011-03-15"
                },
                {
                    "client_op_id": "op-insc-003",
                    "type": "inscription",
                    "local_student_uuid": "uuid-temp-003",
                    "classe_id": self.classe_a.id,
                    "annee_scolaire_id": self.annee_a.id,
                    "frais_annuels": 160000.0
                }
            ]
        }

        resp = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["processed"], 2)

        # Récupération de l'élève créé
        eleve = Eleve.query.filter_by(nom="Salifou", prenom="Ali", ecole_id=self.ecole_a.id).first()
        self.assertIsNotNone(eleve)

        # Vérification que l'inscription a été liée au vrai ID élève
        insc = Inscription.query.filter_by(eleve_id=eleve.id, classe_id=self.classe_a.id).first()
        self.assertIsNotNone(insc)
        self.assertEqual(insc.frais_annuels, 160000.0)
        self.assertEqual(eleve.classe_id, self.classe_a.id)

    # 4. Modification élève avec base_version périmée -> conflict
    def test_04_modification_eleve_version_conflict(self):
        self._login(self.admin_a.email)

        eleve = Eleve(
            nom="Bello",
            prenom="Aminou",
            date_naissance=date(2012, 4, 1),
            ecole_id=self.ecole_a.id
        )
        db.session.add(eleve)
        db.session.commit()

        # Première modification enregistrée (version = 2)
        log = SyncOperationLog(
            client_op_id="op-mod-first",
            ecole_id=self.ecole_a.id,
            user_id=self.admin_a.id,
            entity_type="eleve",
            entity_id=eleve.id,
            status="synced"
        )
        db.session.add(log)
        db.session.commit()

        # Tentative de modification basée sur l'ancienne base_version (1 alors que current_version = 2)
        payload = {
            "operations": [
                {
                    "client_op_id": "op-mod-stale",
                    "type": "eleve_modification",
                    "eleve_id": eleve.id,
                    "nom": "Bello Modifié",
                    "base_version": 1
                }
            ]
        }

        resp = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        res = data["results"][0]
        self.assertEqual(res["status"], "conflict")
        self.assertEqual(res["server_version"], 2)

    # 5. Note / absence existantes continuent à synchroniser
    def test_05_note_et_absence_sync_toujours_ok(self):
        # Création d'un élève inscrit
        eleve = Eleve(
            nom="Sani",
            prenom="Moussa",
            date_naissance=date(2012, 1, 1),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_a.id
        )
        db.session.add(eleve)
        db.session.commit()

        insc = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=eleve.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id,
            statut="inscrit"
        )
        db.session.add(insc)
        db.session.commit()

        self._login(self.user_prof_a.email)

        payload = {
            "operations": [
                {
                    "client_op_id": "op-note-005",
                    "type": "note",
                    "eleve_id": eleve.id,
                    "cours_id": self.cours_math.id,
                    "valeur": 16.5,
                    "date_evaluation": "2024-11-10",
                    "type_evaluation": "Devoir",
                    "coefficient": 2.0
                },
                {
                    "client_op_id": "op-abs-005",
                    "type": "absence",
                    "eleve_id": eleve.id,
                    "cours_id": self.cours_math.id,
                    "date_absence": "2024-11-12",
                    "motif": "Maladie",
                    "justifiee": True
                }
            ]
        }

        resp = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["processed"], 2)

        note = Note.query.filter_by(eleve_id=eleve.id, cours_id=self.cours_math.id).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.valeur, 16.5)

        absence = Absence.query.filter_by(eleve_id=eleve.id, date_absence=date(2024, 11, 12)).first()
        self.assertIsNotNone(absence)
        self.assertTrue(absence.justifiee)

    # 6. Paiement / année / structure rejetés par /api/sync (ONLINE ONLY)
    def test_06_operations_financieres_et_structure_rejet_sync(self):
        self._login(self.admin_a.email)

        payload = {
            "operations": [
                {
                    "client_op_id": "op-pay-refused",
                    "type": "paiement",
                    "eleve_id": 1,
                    "montant": 50000.0,
                    "mois": 10,
                    "annee": 2024
                },
                {
                    "client_op_id": "op-year-refused",
                    "type": "annee_scolaire",
                    "nom": "2025-2026"
                },
                {
                    "client_op_id": "op-struct-refused",
                    "type": "classe",
                    "nom": "5ème A"
                }
            ]
        }

        resp = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for res in data["results"]:
            self.assertEqual(res["status"], "forbidden")
            self.assertIn("Connexion Internet requise", res["message"])

    # 7. Logout + autre utilisateur -> aucune donnée privée précédente accessible
    def test_07_logout_isolation_donnees_privees(self):
        # Création enfant pour Parent A
        eleve_a = Eleve(
            nom="Enfant",
            prenom="ParentA",
            date_naissance=date(2014, 2, 2),
            ecole_id=self.ecole_a.id,
            parent_id=self.parent_a.id,
            classe_id=self.classe_a.id
        )
        db.session.add(eleve_a)
        db.session.commit()

        # Login Parent A -> a accès à ses enfants
        self._login(self.parent_a.email)
        resp_parent = self.client.get("/api/parent/offline-data")
        self.assertEqual(resp_parent.status_code, 200)
        data_parent = resp_parent.get_json()
        self.assertEqual(len(data_parent["enfants"]), 1)
        self.assertEqual(data_parent["enfants"][0]["nom"], "Enfant")

        # Parent ne peut jamais poster dans /api/sync
        post_parent = self.client.post("/api/sync", json={"operations": []})
        self.assertIn(post_parent.status_code, (403, 302))

        # Logout
        self._logout()

        # Tentative d'accès non connecté -> 302 ou 401
        resp_anon = self.client.get("/api/parent/offline-data")
        self.assertIn(resp_anon.status_code, (302, 401))

    # 8. École A / École B -> isolation stricte multi-écoles
    def test_08_isolation_multi_ecoles_sync(self):
        # Admin B tente de synchroniser une note pour un élève de l'École A
        eleve_a = Eleve(
            nom="Garba",
            prenom="Issa",
            date_naissance=date(2012, 6, 6),
            ecole_id=self.ecole_a.id,
            classe_id=self.classe_a.id
        )
        db.session.add(eleve_a)
        db.session.commit()

        # Login Admin B
        self._login(self.admin_b.email)

        payload = {
            "operations": [
                {
                    "client_op_id": "op-cross-school-008",
                    "type": "note",
                    "eleve_id": eleve_a.id,
                    "cours_id": self.cours_math.id,
                    "valeur": 18.0,
                    "date_evaluation": "2024-11-15"
                },
                {
                    "client_op_id": "op-cross-eleve-mod",
                    "type": "eleve_modification",
                    "eleve_id": eleve_a.id,
                    "nom": "Piratage École A"
                }
            ]
        }

        resp = self.client.post("/api/sync", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        for res in data["results"]:
            self.assertIn(res["status"], ("forbidden", "error"))

        # Vérification qu'aucune modification n'a été appliquée à l'élève de l'école A
        eleve_reloaded = Eleve.query.get(eleve_a.id)
        self.assertEqual(eleve_reloaded.nom, "Garba")
        self.assertEqual(len(eleve_reloaded.notes), 0)


if __name__ == "__main__":
    unittest.main()
