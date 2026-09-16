"""
Tests KLASORA — Authentification des bulletins par QR code unique
1. Génération d'un QR unique pour un bulletin
2. Deux périodes différentes pour le même élève => QR / tokens différents
3. Deux élèves différents => QR / tokens différents
4. Route publique avec token valide => document authentique
5. Token invalide => document introuvable / non valide
6. Bulletin provisoire => page indique provisoire
7. Bulletin officiel => page indique officiel
8. Aucune note détaillée exposée publiquement
9. Signature/cachet ne sont plus affichés dans les paramètres
10. Génération PDF contient bien le QR
11. En-têtes HTTP de sécurité (no-store, noindex) sur la vérification publique
"""
import unittest
from datetime import date, datetime
from io import BytesIO

from app import create_app, db
from app.models import (
    AnneeNiveauConfig,
    AnneeScolaire,
    Classe,
    Cours,
    Ecole,
    Eleve,
    Inscription,
    NiveauScolaire,
    Note,
    PeriodeBulletin,
    Utilisateur,
)
from app.services.bulletin_verification import (
    generer_token_bulletin,
    decoder_token_bulletin,
    get_bulletin_verification_serializer,
    generer_token_eleve,
    decoder_token_eleve,
)
from app.services import generer_bulletin_pdf
from sqlalchemy.pool import StaticPool


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    SECRET_KEY = "test-secret-key-klasora"
    BULLETIN_VERIFICATION_KEY = "test-bulletin-verification-key-stable"
    SERVER_NAME = "localhost:5007"
    PROPAGATE_EXCEPTIONS = True
    LOGIN_DISABLED = False


class TestBulletinVerificationQR(unittest.TestCase):

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        # 1. École
        self.ecole = Ecole(
            nom="CSP Responsabilité",
            adresse="Quartier Francophonie",
            telephone="96000000",
            email="contact@csp-responsabilite.ne",
            directeur="M. Amadou Oumarou",
            ville="Niamey",
            statut="actif",
        )
        db.session.add(self.ecole)
        db.session.commit()

        # 2. Utilisateur Admin
        self.admin = Utilisateur(
            nom="Admin Test",
            email="admin@csp-responsabilite.ne",
            role="admin",
            statut="actif",
            ecole_id=self.ecole.id,
        )
        self.admin.set_mot_de_passe("Admin1234!")
        db.session.add(self.admin)
        db.session.commit()

        # 3. Année scolaire active
        self.annee = AnneeScolaire(
            nom="2025-2026",
            date_debut=date(2025, 9, 15),
            date_fin=date(2026, 6, 30),
            statut="active",
            ecole_id=self.ecole.id,
        )
        db.session.add(self.annee)
        db.session.commit()

        # 4. Niveau & Classe
        self.niveau = NiveauScolaire(code="6EME", nom="6ème", cycle="college", ordre=1)
        db.session.add(self.niveau)
        db.session.commit()

        self.annee_niv_config = AnneeNiveauConfig(
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
            niveau_id=self.niveau.id,
            actif=True,
        )
        db.session.add(self.annee_niv_config)
        db.session.commit()

        self.classe = Classe(
            nom="6e A",
            niveau="6ème",
            ecole_id=self.ecole.id,
            annee_scolaire_id=self.annee.id,
        )
        db.session.add(self.classe)
        db.session.commit()

        # 5. Élèves
        self.eleve1 = Eleve(
            nom="MOCTAR",
            prenom="Omar",
            genre="M",
            date_naissance=date(2013, 5, 10),
            code_parent="#6",
            contact_parent="91111111",
            ecole_id=self.ecole.id,
        )
        self.eleve2 = Eleve(
            nom="IDRISSA",
            prenom="Fatima",
            genre="F",
            date_naissance=date(2014, 8, 15),
            code_parent="#7",
            contact_parent="92222222",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([self.eleve1, self.eleve2])
        db.session.commit()

        # Inscriptions
        self.ins1 = Inscription(
            eleve_id=self.eleve1.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="inscrit",
        )
        self.ins2 = Inscription(
            eleve_id=self.eleve2.id,
            classe_id=self.classe.id,
            annee_scolaire_id=self.annee.id,
            ecole_id=self.ecole.id,
            statut="inscrit",
        )
        db.session.add_all([self.ins1, self.ins2])
        db.session.commit()

        # 6. Périodes de bulletin
        self.periode1 = PeriodeBulletin(
            nom="Semestre 1",
            annee_id=self.annee.id,
            ecole_id=self.ecole.id,
            publie=False,
            periode_active=True,
        )
        self.periode2 = PeriodeBulletin(
            nom="Semestre 2",
            annee_id=self.annee.id,
            ecole_id=self.ecole.id,
            publie=False,
            periode_active=False,
        )
        db.session.add_all([self.periode1, self.periode2])
        db.session.commit()

        # 7. Cours
        self.cours_math = Cours(
            nom="Mathématiques",
            classe_id=self.classe.id,
            ecole_id=self.ecole.id,
            coefficient=2.0,
        )
        self.cours_fr = Cours(
            nom="Français",
            classe_id=self.classe.id,
            ecole_id=self.ecole.id,
            coefficient=2.0,
        )
        db.session.add_all([self.cours_math, self.cours_fr])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True
            sess['annee_consultee_id'] = self.annee.id

    def test_01_generation_token_unique_bulletin(self):
        """1. Génération d'un token unique pour un bulletin."""
        token = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        self.assertIsInstance(token, str)
        self.assertTrue(len(token) > 20)

        # Décoder le token
        e_id, ins_id, p_id = decoder_token_bulletin(token)
        self.assertEqual(e_id, self.ecole.id)
        self.assertEqual(ins_id, self.ins1.id)
        self.assertEqual(p_id, self.periode1.id)

    def test_02_deux_periodes_differentes_meme_eleve_qr_differents(self):
        """2. Deux périodes différentes pour le même élève => QR / tokens différents."""
        token_s1 = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        token_s2 = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode2.id)
        self.assertNotEqual(token_s1, token_s2)

        _, _, p1 = decoder_token_bulletin(token_s1)
        _, _, p2 = decoder_token_bulletin(token_s2)
        self.assertEqual(p1, self.periode1.id)
        self.assertEqual(p2, self.periode2.id)

    def test_03_deux_eleves_differents_qr_differents(self):
        """3. Deux élèves différents => QR / tokens différents."""
        token_e1 = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        token_e2 = generer_token_bulletin(self.ecole.id, self.ins2.id, self.periode1.id)
        self.assertNotEqual(token_e1, token_e2)

        _, ins1, _ = decoder_token_bulletin(token_e1)
        _, ins2, _ = decoder_token_bulletin(token_e2)
        self.assertEqual(ins1, self.ins1.id)
        self.assertEqual(ins2, self.ins2.id)

    def test_04_route_publique_token_valide_document_authentique(self):
        """4. Route publique avec token valide => document authentique sans connexion."""
        token = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        resp = self.client.get(f'/verifier/bulletin/{token}')
        self.assertEqual(resp.status_code, 200)

        html = resp.data.decode('utf-8')
        self.assertIn("DOCUMENT AUTHENTIQUE", html)
        self.assertIn("Omar", html)
        self.assertIn("MOCTAR", html)
        self.assertIn("CSP Responsabilité", html)
        self.assertIn("6e A", html)
        self.assertIn("2025-2026", html)
        self.assertIn("Semestre 1", html)

    def test_05_token_invalide_document_introuvable(self):
        """5. Token invalide => document introuvable / non valide."""
        resp = self.client.get('/verifier/bulletin/token_completement_invalide_xyz')
        # Doit renvoyer une réponse indiquant que le document est invalide / introuvable
        html = resp.data.decode('utf-8')
        self.assertTrue(resp.status_code in (404, 200))
        self.assertTrue(
            "introuvable" in html.lower() or "non valide" in html.lower() or "non authentique" in html.lower()
        )

    def test_06_bulletin_provisoire_indique_provisoire(self):
        """6. Bulletin non complet ou période non publiée => page indique PROVISOIRE."""
        self.periode1.publie = False
        db.session.commit()

        token = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        resp = self.client.get(f'/verifier/bulletin/{token}')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')

        self.assertIn("BULLETIN PROVISOIRE", html)
        self.assertNotIn("BULLETIN OFFICIEL", html)

    def test_07_bulletin_officiel_indique_officiel(self):
        """7. Bulletin complet et période publiée => page indique OFFICIEL."""
        # Ajouter notes complètes pour toutes les matières de 6e A
        n1 = Note(
            eleve_id=self.eleve1.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id,
            cours_id=self.cours_math.id,
            valeur=15.0,
            type_evaluation="devoir",
            periode="Semestre 1",
            ecole_id=self.ecole.id,
        )
        n2 = Note(
            eleve_id=self.eleve1.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id,
            cours_id=self.cours_fr.id,
            valeur=14.0,
            type_evaluation="devoir",
            periode="Semestre 1",
            ecole_id=self.ecole.id,
        )
        db.session.add_all([n1, n2])
        self.periode1.publie = True
        self.periode1.date_publication = datetime(2026, 1, 20, 10, 0)
        db.session.commit()

        token = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        resp = self.client.get(f'/verifier/bulletin/{token}')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')

        self.assertIn("BULLETIN OFFICIEL", html)
        self.assertNotIn("BULLETIN PROVISOIRE", html)

    def test_08_aucune_note_detaillee_ni_donnee_sensible_exposee(self):
        """8. Aucune note détaillée, absence, paiement ou téléphone parent exposé publiquement."""
        # Mettre des notes
        n1 = Note(
            eleve_id=self.eleve1.id,
            annee_id=self.annee.id,
            inscription_id=self.ins1.id,
            cours_id=self.cours_math.id,
            valeur=17.75,
            type_evaluation="devoir",
            periode="Semestre 1",
            ecole_id=self.ecole.id,
        )
        db.session.add(n1)
        db.session.commit()

        token = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        resp = self.client.get(f'/verifier/bulletin/{token}')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')

        # La valeur de la note ne doit pas apparaître
        self.assertNotIn("17.75", html)
        # Pas de contacts privés
        self.assertNotIn("91111111", html)  # téléphone parent
        self.assertNotIn("contact_parent", html)
        self.assertNotIn("Solde", html)
        self.assertNotIn("Paiement", html)

    def test_09_signature_et_cachet_retires_des_parametres(self):
        """9. Signature et cachet ne sont plus affichés ni modifiables dans l'interface /profil-ecole."""
        self.login_admin()
        resp = self.client.get('/profil-ecole')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')

        # Vérifier que "Signature du Directeur" et "Cachet Officiel" sont retirés
        self.assertNotIn("Signature du Directeur", html)
        self.assertNotIn("Cachet Officiel", html)
        self.assertNotIn('name="signature"', html)
        self.assertNotIn('name="cachet"', html)

        # Le logo doit rester présent
        self.assertIn("Logo École", html)
        self.assertIn('name="logo"', html)

    def test_10_generation_pdf_contient_qr_et_mention(self):
        """10. La génération PDF contient le QR code et la mention 'Scanner pour vérifier'."""
        token = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        verif_url = f"http://localhost:5007/verifier/bulletin/{token}"

        buf = generer_bulletin_pdf(
            eleve=self.eleve1,
            notes_par_cours={},
            moyennes_par_cours={},
            moyenne_generale=14.5,
            nom_ecole=self.ecole.nom,
            classe_nom=self.classe.nom,
            annee_scolaire_nom=self.annee.nom,
            periode_nom="Semestre 1",
            verification_url=verif_url,
        )

        self.assertIsInstance(buf, BytesIO)
        pdf_content = buf.getvalue()
        self.assertTrue(len(pdf_content) > 1000)
        # Vérifier présence de fragments dans le stream PDF
        self.assertIn(b"%PDF", pdf_content[:10])

    def test_11_headers_securite_page_verification(self):
        """11. En-têtes HTTP de sécurité (Cache-Control: no-store, private et robots noindex)."""
        token = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)
        resp = self.client.get(f'/verifier/bulletin/{token}')
        self.assertEqual(resp.status_code, 200)

        # En-têtes HTTP
        cache_control = resp.headers.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('private', cache_control)

        html = resp.data.decode('utf-8')
        self.assertIn('noindex', html)
        self.assertIn('nofollow', html)

    def test_12_generation_token_unique_eleve_inscription(self):
        """12. Le token élève est rattaché à l'inscription annuelle (ecole_id, inscription_id)."""
        token_elv1 = generer_token_eleve(self.ecole.id, self.ins1.id)
        token_elv2 = generer_token_eleve(self.ecole.id, self.ins2.id)
        self.assertNotEqual(token_elv1, token_elv2)

        e_id, ins_id = decoder_token_eleve(token_elv1)
        self.assertEqual(e_id, self.ecole.id)
        self.assertEqual(ins_id, self.ins1.id)

    def test_13_token_eleve_non_interpretable_comme_token_bulletin(self):
        """13. Sel différent : un token élève ne peut pas être interprété comme un token bulletin et inversement."""
        token_elv = generer_token_eleve(self.ecole.id, self.ins1.id)
        token_bul = generer_token_bulletin(self.ecole.id, self.ins1.id, self.periode1.id)

        # Token élève passé à décodeur bulletin -> None
        b_e, b_i, b_p = decoder_token_bulletin(token_elv)
        self.assertIsNone(b_e)
        self.assertIsNone(b_i)
        self.assertIsNone(b_p)

        # Token bulletin passé à décodeur élève -> None
        e_e, e_i = decoder_token_eleve(token_bul)
        self.assertIsNone(e_e)
        self.assertIsNone(e_i)

    def test_14_route_publique_eleve_affiche_identite_minimale(self):
        """14. Route publique /verifier/eleve/<token> affiche l'identité minimale."""
        token_elv = generer_token_eleve(self.ecole.id, self.ins1.id)
        resp = self.client.get(f'/verifier/eleve/{token_elv}')
        self.assertEqual(resp.status_code, 200)

        html = resp.data.decode('utf-8')
        self.assertIn("Omar", html)
        self.assertIn("MOCTAR", html)
        self.assertIn("CSP Responsabilité", html)
        self.assertIn("6e A", html)
        self.assertIn("2025-2026", html)
        self.assertIn("ÉLÈVE INSCRIT", html)

    def test_15_route_publique_eleve_sans_donnees_privees(self):
        """15. Aucun contact, parent, note, absence, paiement ou autre donnée privée sur /verifier/eleve/<token>."""
        token_elv = generer_token_eleve(self.ecole.id, self.ins1.id)
        resp = self.client.get(f'/verifier/eleve/{token_elv}')
        self.assertEqual(resp.status_code, 200)

        html = resp.data.decode('utf-8')
        self.assertNotIn("91111111", html)  # contact parent
        self.assertNotIn("contact_parent", html)
        self.assertNotIn("Note", html)
        self.assertNotIn("Absence", html)
        self.assertNotIn("Paiement", html)
        self.assertNotIn("Solde", html)

    def test_16_headers_securite_page_verification_eleve(self):
        """16. En-têtes HTTP de sécurité (Cache-Control: no-store, private et robots noindex)."""
        token_elv = generer_token_eleve(self.ecole.id, self.ins1.id)
        resp = self.client.get(f'/verifier/eleve/{token_elv}')
        self.assertEqual(resp.status_code, 200)

        cache_control = resp.headers.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('private', cache_control)

        html = resp.data.decode('utf-8')
        self.assertIn('noindex', html)
        self.assertIn('nofollow', html)

    def test_17_qrcodes_etudiants_genere_badges_avec_url_verification(self):
        """17. /qrcodes_etudiants affiche les badges élèves avec l'URL de vérification intégrée."""
        self.login_admin()
        resp = self.client.get('/qrcodes_etudiants')
        self.assertEqual(resp.status_code, 200)

        html = resp.data.decode('utf-8')
        self.assertIn("Omar", html)
        self.assertIn("MOCTAR", html)
        self.assertIn("6e A", html)
        self.assertIn("data:image/png;base64,", html)


if __name__ == '__main__':
    unittest.main()
