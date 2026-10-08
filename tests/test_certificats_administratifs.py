import unittest
from datetime import date
from sqlalchemy.pool import StaticPool
from werkzeug.security import generate_password_hash
from app import create_app, db
from app.config import Config
from app.models import Utilisateur, Ecole, AnneeScolaire, Classe, Eleve, Inscription, CertificatAdministratif


class CertificatsTestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-certificats"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


class TestCertificatsAdministratifs(unittest.TestCase):
    def setUp(self):
        self.app = create_app(CertificatsTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # Établissement A
        self.ecole_a = Ecole(nom="Lycée d'Excellence Niamey", onboarding_complete=True, adresse="Quartier Plateau, Niamey")
        # Établissement B (pour tester le cloisonnement étanche)
        self.ecole_b = Ecole(nom="Collège Privé Espoir", onboarding_complete=True, adresse="Quartier Yantala, Niamey")
        db.session.add_all([self.ecole_a, self.ecole_b])
        db.session.commit()

        # Année scolaire active Ecole A
        self.annee_a = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 1),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole_a.id
        )
        db.session.add(self.annee_a)
        db.session.commit()

        # Classe
        self.classe_a = Classe(nom="Terminale C", ecole_id=self.ecole_a.id, niveau="Lycée")
        db.session.add(self.classe_a)
        db.session.commit()

        # Élève
        self.eleve = Eleve(
            nom="IDRISSA",
            prenom="Fatima",
            genre="F",
            date_naissance=date(2008, 4, 15),
            lieu_naissance="Niamey",
            code_parent="MAT-2025-089",
            statut="actif",
            ecole_id=self.ecole_a.id
        )
        db.session.add(self.eleve)
        db.session.commit()

        # Inscription
        self.inscription = Inscription(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            classe_id=self.classe_a.id,
            annee_scolaire_id=self.annee_a.id,
            statut="inscrit"
        )
        db.session.add(self.inscription)
        db.session.commit()

        # Utilisateur Admin Ecole A
        self.admin_a = Utilisateur(
            nom="OUSMANE",
            prenom="Ibrahim",
            email="admin.ibrahim@excellence.edu",
            role="admin",
            ecole_id=self.ecole_a.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Admin1234!")
        )
        # Utilisateur Admin Ecole B
        self.admin_b = Utilisateur(
            nom="SOULEY",
            prenom="Amadou",
            email="admin.souley@espoir.edu",
            role="admin",
            ecole_id=self.ecole_b.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Admin1234!")
        )
        # Utilisateur Parent
        self.parent_user = Utilisateur(
            nom="IDRISSA",
            prenom="Moussa",
            email="parent.moussa@gmail.com",
            role="parent",
            ecole_id=self.ecole_a.id,
            statut="actif",
            mot_de_passe=generate_password_hash("Parent1234!")
        )
        db.session.add_all([self.admin_a, self.admin_b, self.parent_user])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_admin_a(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_a.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole_a.id
            sess['role'] = 'admin'
            sess['onboarding_complete'] = True
            sess['onboarding_complete_' + str(self.ecole_a.id)] = True
            sess['annee_consultee'] = {str(self.ecole_a.id): self.annee_a.id}

    def login_admin_b(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_b.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole_b.id
            sess['role'] = 'admin'
            sess['onboarding_complete'] = True
            sess['onboarding_complete_' + str(self.ecole_b.id)] = True

    def login_parent(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.parent_user.id)
            sess['_fresh'] = True
            sess['ecole_id'] = self.ecole_a.id
            sess['role'] = 'parent'

    # =========================================================================
    # 1. TEST MODÈLE
    # =========================================================================
    def test_certificat_modele_creation(self):
        """Vérifie la persistance et les relations du modèle CertificatAdministratif."""
        cert = CertificatAdministratif(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            type_certificat="scolarite",
            reference="CS-2025-0001",
            annee_scolaire="2025-2026",
            classe_nom="Terminale C",
            niveau="Lycée",
            ville_emission="Niamey",
            date_emission=date.today(),
            signataire_nom="M. Ousmane Ibrahim",
            signataire_titre="Le Proviseur",
            code_verification="abcdef1234567890abcdef1234567890"
        )
        db.session.add(cert)
        db.session.commit()

        self.assertIsNotNone(cert.id)
        self.assertEqual(cert.reference, "CS-2025-0001")
        self.assertEqual(cert.eleve.nom, "IDRISSA")
        self.assertEqual(cert.ecole.nom, "Lycée d'Excellence Niamey")

    # =========================================================================
    # 2. TEST GÉNÉRATION (POST /certificats/generer)
    # =========================================================================
    def test_generer_certificat_scolarite(self):
        """Génération d'un certificat de scolarité avec référence CS-YYYY-XXXX."""
        self.login_admin_a()

        payload = {
            "eleve_id": self.eleve.id,
            "type_certificat": "scolarite",
            "ville_emission": "Niamey",
            "date_emission": str(date.today()),
            "signataire_nom": "M. Ousmane Ibrahim",
            "signataire_titre": "Le Proviseur"
        }

        resp = self.client.post("/certificats/generer", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("redirect_url", data)

        # Vérifier en base
        cert = CertificatAdministratif.query.filter_by(eleve_id=self.eleve.id).first()
        self.assertIsNotNone(cert)
        self.assertEqual(cert.type_certificat, "scolarite")
        self.assertTrue(cert.reference.startswith(f"CS-{date.today().year}-"))
        self.assertTrue(len(cert.code_verification) >= 16)
        self.assertEqual(cert.signataire_nom, "M. Ousmane Ibrahim")
        self.assertEqual(cert.classe_nom, "Terminale C")

    def test_generer_certificat_inscription(self):
        """Génération d'un certificat d'inscription/réinscription avec référence CI-YYYY-XXXX."""
        self.login_admin_a()

        payload = {
            "eleve_id": self.eleve.id,
            "type_certificat": "inscription",
            "type_admission": "Réinscription",
            "ville_emission": "Niamey",
            "date_emission": str(date.today()),
            "signataire_nom": "M. Ousmane Ibrahim",
            "signataire_titre": "Le Proviseur"
        }

        resp = self.client.post("/certificats/generer", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

        cert = CertificatAdministratif.query.filter_by(eleve_id=self.eleve.id, type_certificat="inscription").first()
        self.assertIsNotNone(cert)
        self.assertTrue(cert.reference.startswith(f"CI-{date.today().year}-"))
        self.assertEqual(cert.type_admission, "Réinscription")

    def test_generer_certificat_transfert(self):
        """Génération d'un certificat de transfert/radiation avec référence CR-YYYY-XXXX et destination."""
        self.login_admin_a()

        payload = {
            "eleve_id": self.eleve.id,
            "type_certificat": "transfert",
            "date_depart": "2025-11-01",
            "etablissement_destination": "Lycée Kassaï de Maradi",
            "ville_emission": "Niamey",
            "date_emission": str(date.today()),
            "signataire_nom": "M. Ousmane Ibrahim",
            "signataire_titre": "Le Proviseur"
        }

        resp = self.client.post("/certificats/generer", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

        cert = CertificatAdministratif.query.filter_by(eleve_id=self.eleve.id, type_certificat="transfert").first()
        self.assertIsNotNone(cert)
        self.assertTrue(cert.reference.startswith(f"CR-{date.today().year}-"))
        self.assertEqual(str(cert.date_depart), "2025-11-01")
        self.assertEqual(cert.etablissement_destination, "Lycée Kassaï de Maradi")

    def test_incrementation_numerotation_automatique(self):
        """Vérifie l'incrémentation séquentielle du numéro de référence (0001 puis 0002)."""
        self.login_admin_a()
        year = date.today().year

        payload = {
            "eleve_id": self.eleve.id,
            "type_certificat": "scolarite",
            "signataire_nom": "M. Ousmane Ibrahim",
            "signataire_titre": "Le Proviseur"
        }

        resp1 = self.client.post("/certificats/generer", json=payload)
        self.assertEqual(resp1.status_code, 200)
        cert1 = CertificatAdministratif.query.order_by(CertificatAdministratif.id.asc()).first()
        self.assertEqual(cert1.reference, f"CS-{year}-0001")

        resp2 = self.client.post("/certificats/generer", json=payload)
        self.assertEqual(resp2.status_code, 200)
        cert2 = CertificatAdministratif.query.order_by(CertificatAdministratif.id.desc()).first()
        self.assertEqual(cert2.reference, f"CS-{year}-0002")

    # =========================================================================
    # 3. TEST IMPRESSION OFFICIELLE (GET /certificats/imprimer/<id>)
    # =========================================================================
    def test_impression_certificat_rendu_et_qr_code(self):
        """Vérifie que le gabarit officiel s'affiche avec le cadre supérieur noir et le QR code base64."""
        self.login_admin_a()

        cert = CertificatAdministratif(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            type_certificat="scolarite",
            reference="CS-2025-0042",
            annee_scolaire="2025-2026",
            classe_nom="Terminale C",
            niveau="Secondaire",
            ville_emission="Niamey",
            date_emission=date.today(),
            signataire_nom="M. Ousmane Ibrahim",
            signataire_titre="Le Proviseur",
            code_verification="test-token-verification-12345"
        )
        db.session.add(cert)
        db.session.commit()

        resp = self.client.get(f"/certificats/imprimer/{cert.id}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Vérification des mentions légales et structurelles
        self.assertIn("CERTIFICAT DE SCOLARITÉ", html)
        self.assertIn("Fatima", html)
        self.assertIn("IDRISSA", html)
        self.assertIn("CS-2025-0042", html)
        self.assertIn("Terminale C", html)
        self.assertIn("M. Ousmane Ibrahim", html)
        self.assertIn("Le Proviseur", html)
        # Vérification de la présence du QR code embarqué base64
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Scannez pour", html)

    # =========================================================================
    # 4. TEST VÉRIFICATION PUBLIQUE SANS AUTHENTIFICATION
    # =========================================================================
    def test_verification_publique_certificat_authentique(self):
        """Accès public sans login à l'URL de vérification QR code avec badge authentique."""
        cert = CertificatAdministratif(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            type_certificat="scolarite",
            reference="CS-2025-0099",
            annee_scolaire="2025-2026",
            classe_nom="Terminale C",
            niveau="Lycée",
            ville_emission="Niamey",
            date_emission=date.today(),
            signataire_nom="M. Ousmane Ibrahim",
            signataire_titre="Le Proviseur",
            code_verification="valid-security-token-998877"
        )
        db.session.add(cert)
        db.session.commit()

        # Requête sans session utilisateur
        resp = self.client.get("/certificats/verifier/valid-security-token-998877")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Document Officiel Authentifi", html)
        self.assertIn("CS-2025-0099", html)
        self.assertIn("Lyc", html)
        self.assertIn("IDRISSA", html)

    def test_verification_publique_certificat_invalide(self):
        """Vérifie le retour pour un code falsifié ou inexistant."""
        resp = self.client.get("/certificats/verifier/fake-token-inconnu-999")
        self.assertEqual(resp.status_code, 404)
        html = resp.get_data(as_text=True)

        self.assertIn("Document Invalide ou Inconnu", html)
        self.assertIn("falsification", html.lower())

    # =========================================================================
    # 5. SÉCURITÉ, ISOLATION MULTI-ÉTABLISSEMENT & ACCÈS RÔLES
    # =========================================================================
    def test_securite_acces_non_admin_interdit(self):
        """Un utilisateur parent ou non connecté ne peut pas générer de certificat."""
        # Unauthenticated
        resp_anon = self.client.post("/certificats/generer", json={"eleve_id": self.eleve.id, "type_certificat": "scolarite"})
        self.assertIn(resp_anon.status_code, [302, 401, 403])

        # Parent
        self.login_parent()
        resp_parent = self.client.post("/certificats/generer", json={"eleve_id": self.eleve.id, "type_certificat": "scolarite"})
        self.assertIn(resp_parent.status_code, [302, 403])

    def test_isolation_multi_etablissement(self):
        """L'administrateur de l'école B ne peut pas imprimer un certificat de l'école A."""
        cert = CertificatAdministratif(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            type_certificat="scolarite",
            reference="CS-2025-0007",
            annee_scolaire="2025-2026",
            classe_nom="Terminale C",
            niveau="Lycée",
            signataire_nom="M. Ousmane Ibrahim",
            signataire_titre="Le Proviseur",
            code_verification="token-ecole-a-secret"
        )
        db.session.add(cert)
        db.session.commit()

        self.login_admin_b()
        resp = self.client.get(f"/certificats/imprimer/{cert.id}")
        self.assertEqual(resp.status_code, 403)

    # =========================================================================
    # 6. REGISTRE AUDIT & RECHERCHE (GET /certificats/registre)
    # =========================================================================
    def test_registre_recherche_et_filtres(self):
        """Vérifie la consultation du registre d'audit et le filtrage."""
        self.login_admin_a()

        c1 = CertificatAdministratif(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            type_certificat="scolarite",
            reference="CS-2025-0101",
            annee_scolaire="2025-2026",
            classe_nom="Terminale C",
            signataire_nom="M. Ousmane Ibrahim",
            signataire_titre="Le Proviseur",
            code_verification="token-101"
        )
        c2 = CertificatAdministratif(
            ecole_id=self.ecole_a.id,
            eleve_id=self.eleve.id,
            type_certificat="inscription",
            reference="CI-2025-0102",
            annee_scolaire="2025-2026",
            classe_nom="Terminale C",
            signataire_nom="M. Ousmane Ibrahim",
            signataire_titre="Le Proviseur",
            code_verification="token-102"
        )
        db.session.add_all([c1, c2])
        db.session.commit()

        # Liste complète
        resp = self.client.get("/certificats/registre")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("CS-2025-0101", html)
        self.assertIn("CI-2025-0102", html)

        # Filtre type scolarite
        resp_filter = self.client.get("/certificats/registre?type=scolarite")
        self.assertEqual(resp_filter.status_code, 200)
        html_filter = resp_filter.get_data(as_text=True)
        self.assertIn("CS-2025-0101", html_filter)
        self.assertNotIn("CI-2025-0102", html_filter)

    # =========================================================================
    # 7. API INFO ÉLÈVE (GET /certificats/eleve/<id>/info)
    # =========================================================================
    def test_api_info_eleve(self):
        """Vérifie le retour JSON de l'API d'information pré-remplissage."""
        self.login_admin_a()
        resp = self.client.get(f"/certificats/eleve/{self.eleve.id}/info")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["eleve"]["nom"], "IDRISSA")
        self.assertEqual(data["eleve"]["classe_nom"], "Terminale C")
        self.assertEqual(data["eleve"]["annee_scolaire"], "2025-2026")


if __name__ == "__main__":
    unittest.main()
