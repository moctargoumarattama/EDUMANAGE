"""
tests/test_assistant_advanced.py
================================
Tests de validation pour les fonctionnalités avancées de l'Assistant IA :
1. Recherche tolérante aux fautes d'orthographe (Fuzzy matching & accents).
2. Mémoire d'entité en session Flask (résolution d'anaphores : 'Et ses absences ?').
3. Transmission de l'historique conversationnel court multi-tours.
4. Réinitialisation de la mémoire d'entité en session.
"""
import unittest
from unittest.mock import patch

from app import create_app, db
from app.models import Absence, Classe, Ecole, Eleve, Inscription, Note, Utilisateur
from app.routes.assistant import (
    _calculate_eleve_similarity,
    _normalize_text,
    _search_eleves_in_ecole,
)


class TestAssistantAdvancedFeatures(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()

        # Récupération des données existantes pour les tests
        self.ecole = Ecole.query.first()
        self.admin = Utilisateur.query.filter_by(role="admin").first()

        # Si aucun élève n'existe dans la base, on en crée un temporaire
        self.eleve = Eleve.query.filter_by(ecole_id=self.ecole.id).first() if self.ecole else None
        if not self.eleve and self.ecole:
            import datetime as dt
            self.eleve = Eleve(
                nom="Diallo",
                prenom="Mamadou",
                date_naissance=dt.date(2012, 5, 14),
                genre="M",
                ecole_id=self.ecole.id,
                contact_parent="+221770000000",
                statut="actif"
            )
            db.session.add(self.eleve)
            db.session.commit()

    def tearDown(self):
        self.app_context.pop()

    def test_01_text_normalization(self):
        """Vérifie la normalisation NFD sans accents et en minuscules."""
        self.assertEqual(_normalize_text("Hélène"), "helene")
        self.assertEqual(_normalize_text("Aïssatou Bâ"), "aissatou ba")
        self.assertEqual(_normalize_text("ÉLÈVE-123!"), "eleve 123")
        self.assertEqual(_normalize_text("   Mamadou   DIALLO   "), "mamadou diallo")

    def test_02_fuzzy_matching_tolerance(self):
        """Vérifie que la recherche retrouve un élève malgré des fautes de frappe."""
        if not self.eleve:
            self.skipTest("Aucun élève disponible pour le test")

        # Test d'une faute sur le prénom (ex: Omarr au lieu d'Omar ou Mamdou au lieu de Mamadou)
        prenom = self.eleve.prenom
        nom = self.eleve.nom

        # Recherche avec une coquille (lettre répétée ou manquante)
        typo_term = prenom + "r" if not prenom.endswith("r") else prenom[:-1]
        matches = _search_eleves_in_ecole(typo_term, self.eleve.ecole_id)

        self.assertTrue(len(matches) > 0, f"Le fuzzy matching doit retrouver l'élève avec la coquille '{typo_term}'")
        self.assertEqual(matches[0].id, self.eleve.id)

    def test_03_sequential_session_memory_and_anaphora(self):
        """
        Vérifie l'enchaînement de deux questions consécutives :
        - Question 1 : 'Quelles sont les notes de [Nom Élève] ?' -> mémorise l'élève en session.
        - Question 2 : 'Et ses absences ?' -> résout automatiquement l'élève sans répéter son nom.
        """
        if not self.eleve or not self.admin:
            self.skipTest("Données insuffisantes pour le test session")

        # Authentification de l'administrateur dans la session de test
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["_fresh"] = True

        # Tour 1 : Question avec nom d'élève
        with patch("app.routes.assistant.query_assistant", return_value="Voici les notes de l'élève."):
            resp1 = self.client.post("/api/assistant/query-data", json={
                "question": f"Quelles sont les notes de {self.eleve.prenom} ?"
            })
            self.assertEqual(resp1.status_code, 200)
            data1 = resp1.get_json()
            self.assertTrue(data1["success"])

        # Vérification de la mémorisation en session Flask
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("ai_last_eleve_id"), self.eleve.id)
            self.assertEqual(sess.get("ai_last_eleve_nom"), f"{self.eleve.prenom} {self.eleve.nom}")

        # Tour 2 : Question avec anaphore 'Et ses absences ?' (aucun nom spécifié)
        captured_history = []

        def mock_query_assistant(prompt, system_context=None, temperature=0.2, history=None):
            nonlocal captured_history
            captured_history = history
            return f"Voici les absences de {self.eleve.prenom}."

        with patch("app.routes.assistant.query_assistant", side_effect=mock_query_assistant):
            turn1_history = [
                {"role": "user", "content": f"Quelles sont les notes de {self.eleve.prenom} ?"},
                {"role": "assistant", "content": "Voici les notes de l'élève."}
            ]
            resp2 = self.client.post("/api/assistant/query-data", json={
                "question": "Et ses absences ?",
                "history": turn1_history
            })

            self.assertEqual(resp2.status_code, 200)
            data2 = resp2.get_json()
            self.assertTrue(data2["success"])
            self.assertEqual(data2["intention"], "absences")
            self.assertTrue(len(data2["donnees"]) > 0)
            # L'élève ciblé doit être exactement celui mémorisé en session
            self.assertEqual(data2["donnees"][0]["eleve_id"], self.eleve.id)

            # Vérification de la transmission de l'historique
            self.assertEqual(captured_history, turn1_history)

    def test_04_clear_session_endpoint(self):
        """Vérifie la réinitialisation de la mémoire d'entité en session."""
        if not self.admin:
            self.skipTest("Admin non trouvé")

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["ai_last_eleve_id"] = 999
            sess["ai_last_eleve_nom"] = "Test Élève"

        resp = self.client.post("/api/assistant/clear-session")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])

        with self.client.session_transaction() as sess:
            self.assertNotIn("ai_last_eleve_id", sess)
            self.assertNotIn("ai_last_eleve_nom", sess)


if __name__ == "__main__":
    unittest.main()

