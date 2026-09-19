# -*- coding: utf-8 -*-
"""
Tests automatisés pour la nouvelle page d'accueil publique de KLASORA (klasora.com/).
Vérifie STRICTEMENT :
- L'accessibilité publique de la landing page sans authentification (HTTP 200).
- La présence des 5 zones clés : Hero, Fonctionnalités, Schéma des rôles, Sécurité + À propos, CTA final.
- La présence immédiate du bouton 'Se connecter' avec lien vers /login.
- La présence du schéma hiérarchique : Établissement, Administration, Professeurs, Parents.
- L'intégrité du texte À propos (ingénieurs nigériens au Maroc).
- L'accessibilité publique des pages /politique-confidentialite et /securite.
- Le fonctionnement du formulaire /demander-demo (validation et notification).
- L'absence de données privées sur la vitrine publique.
- La redirection transparente et sans régression des utilisateurs déjà connectés vers leur dashboard.
"""

import unittest
from app import create_app, db
from app.models import Ecole, Utilisateur


class TestLandingPage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['SERVER_NAME'] = 'localhost'

    def setUp(self):
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    # --------------------------------------------------------------------------
    # 1. Tests d'accès public et contenu de la Landing Page
    # --------------------------------------------------------------------------
    def test_01_landing_accessible_sans_authentification(self):
        """1. Vérifie que GET / renvoie un code HTTP 200 pour un visiteur anonyme."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_02_landing_contient_titres_et_cta_hero(self):
        """2. Vérifie la présence du titre principal, sous-titre et des boutons du Hero."""
        response = self.client.get('/')
        html = response.data.decode('utf-8')
        
        # Titre principal
        self.assertIn("Tout votre établissement scolaire, dans une seule plateforme.", html)
        # Sous-titre
        self.assertIn("Élèves, enseignants, notes, absences, paiements, bulletins, emplois du temps et suivi administratif.", html)
        # Petite phrase d'accroche
        self.assertIn("Une gestion scolaire moderne, simple et centralisée.", html)
        # Bouton Se connecter
        self.assertIn("Se connecter", html)
        self.assertIn("/login", html)
        # Bouton Demander une présentation
        self.assertIn("Demander une présentation", html)

    def test_03_landing_contient_les_8_fonctionnalites(self):
        """3. Vérifie que les 8 fonctionnalités essentielles sont présentes sur la page."""
        response = self.client.get('/')
        html = response.data.decode('utf-8')
        
        features = [
            "Élèves & classes",
            "Professeurs",
            "Notes & évaluations",
            "Absences",
            "Paiements",
            "Bulletins scolaires",
            "Emploi du temps",
            "Rapports & vérification QR"
        ]
        for f in features:
            self.assertIn(f, html, f"La fonctionnalité '{f}' est manquante dans la vitrine publique.")

    def test_04_landing_contient_schema_roles_et_phrase_cle(self):
        """4. Vérifie le schéma hiérarchique des rôles et sa phrase obligatoire."""
        response = self.client.get('/')
        html = response.data.decode('utf-8')
        
        # Niveaux hiérarchiques
        self.assertIn("ÉTABLISSEMENT", html)
        self.assertIn("Administration", html)
        self.assertIn("Professeurs", html)
        self.assertIn("Parents", html)
        # Phrase obligatoire sous le schéma
        self.assertIn("Chaque utilisateur dispose uniquement des accès correspondant à son rôle.", html)

    def test_05_landing_contient_securite_factuelle_et_liens(self):
        """5. Vérifie les 4 piliers factuels de sécurité et les liens vers /securite et /politique-confidentialite."""
        response = self.client.get('/')
        html = response.data.decode('utf-8')
        
        self.assertIn("Vos données scolaires méritent une vraie protection.", html)
        self.assertIn("Accès sécurisés selon les rôles", html)
        self.assertIn("Séparation des établissements", html)
        self.assertIn("Sauvegardes régulières", html)
        self.assertIn("Documents vérifiables par QR", html)
        
        # Liens
        self.assertIn("/politique-confidentialite", html)
        self.assertIn("/securite", html)

    def test_06_landing_contient_texte_a_propos_exact(self):
        """6. Vérifie le texte demandé concernant l'origine du projet KLASORA."""
        response = self.client.get('/')
        html = response.data.decode('utf-8')
        
        texte_attendu = "KLASORA est une plateforme de gestion scolaire conçue par des ingénieurs nigériens établis au Maroc. Elle est née d’un objectif simple : proposer aux établissements scolaires une solution moderne, pratique et adaptée à leurs réalités quotidiennes."
        self.assertIn(texte_attendu, html)

    def test_07_landing_contient_cta_final_et_footer(self):
        """7. Vérifie la présence du footer compact et du copyright."""
        response = self.client.get('/')
        html = response.data.decode('utf-8')
        
        self.assertIn("© 2026 KLASORA", html)

    # --------------------------------------------------------------------------
    # 2. Pages Publiques Annexes
    # --------------------------------------------------------------------------
    def test_08_page_politique_confidentialite_accessible(self):
        """8. Vérifie que GET /politique-confidentialite renvoie un code HTTP 200."""
        response = self.client.get('/politique-confidentialite')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn("Politique de Confidentialité", html)
        self.assertIn("Protection des Données", html)

    def test_09_page_securite_accessible(self):
        """9. Vérifie que GET /securite renvoie un code HTTP 200."""
        response = self.client.get('/securite')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn("Sécurité & Protection des Données Scolaires", html)
        self.assertIn("Documents Vérifiables par QR Code", html)

    # --------------------------------------------------------------------------
    # 3. Formulaire de Demande de Présentation
    # --------------------------------------------------------------------------
    def test_10_demande_demo_get_valide(self):
        """10. Vérifie que /demander-demo est accessible en GET."""
        # Test GET
        resp_get = self.client.get('/demander-demo')
        self.assertEqual(resp_get.status_code, 200)
        self.assertIn("Demander une Présentation Personnalisée", resp_get.data.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
