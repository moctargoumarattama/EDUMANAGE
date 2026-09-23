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

    def test_05_greeting_interception_without_db(self):
        """Vérifie que l'envoi de 'Bonjour' renvoie une salutation claire sans recherche SQL ni IA."""
        if not self.admin:
            self.skipTest("Admin non trouvé")

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        # Test sur /api/assistant/query-data
        resp = self.client.post("/api/assistant/query-data", json={"question": "Bonjour !"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["intention"], "salutation")
        self.assertEqual(data["donnees_trouvees"], 0)
        self.assertIn("assistant KLASORA", data["reply"])
        self.assertNotIn("base de données", data["reply"].lower())
        self.assertNotIn("null", data["reply"].lower())

        # Test sur /api/assistant/chat
        resp_chat = self.client.post("/api/assistant/chat", json={"message": "salut"})
        self.assertEqual(resp_chat.status_code, 200)
        data_chat = resp_chat.get_json()
        self.assertTrue(data_chat["success"])
        self.assertIn("assistant KLASORA", data_chat["reply"])

    def test_06_merci_and_politeness_interception(self):
        """Vérifie l'interception des remerciements et départs."""
        if not self.admin:
            self.skipTest("Admin non trouvé")

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        resp = self.client.post("/api/assistant/query-data", json={"question": "Merci beaucoup"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("Je vous en prie", data["reply"])

    def test_07_null_sanitization_and_no_target_guard(self):
        """Vérifie qu'une intention avec 'null' comme élève ne cherche jamais d'élève 'null' en base."""
        if not self.admin:
            self.skipTest("Admin non trouvé")

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess.pop("ai_last_eleve_id", None)

        with patch("app.routes.assistant.extract_query_intent", return_value={
            "intention": "notes",
            "classe": "null",
            "eleve": "null",
            "periode": None,
            "seuil": None
        }):
            resp = self.client.post("/api/assistant/query-data", json={
                "question": "Quelles sont les notes ?"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["donnees_trouvees"], 0)
            self.assertIn("Pour quel élève ou quelle classe", data["reply"])
            self.assertNotIn("null", data["reply"].lower())

    def test_08_system_prompt_vocabulary_rules(self):
        """Vérifie la présence de la règle impérative de vocabulaire dans SYSTEM_ASSISTANT."""
        from app.ai_service import SYSTEM_ASSISTANT
        self.assertIn("RÈGLE IMPÉRATIVE DE VOCABULAIRE", SYSTEM_ASSISTANT)
        self.assertIn("Ne mentionne JAMAIS de termes techniques", SYSTEM_ASSISTANT)
        self.assertIn("base de données", SYSTEM_ASSISTANT)

    def test_09_qui_est_eleve_conversational(self):
        """Vérifie que 'Qui est [nom]' ou 'C'est qui [nom]' renvoie le profil humain avec le bouton [Voir son dossier complet]."""
        if not self.eleve or not self.admin:
            self.skipTest("Données insuffisantes")

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)

        resp = self.client.post("/api/assistant/query-data", json={
            "question": f"Qui est {self.eleve.prenom} ?"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["intention"], "fiche_eleve")
        self.assertTrue(data["donnees_trouvees"] > 0)
        self.assertIn(f"[Voir son dossier complet](/eleve/{self.eleve.id})", data["reply"])
        self.assertIn(self.eleve.nom, data["reply"])

    def test_10_qui_est_ce_anaphora_followup(self):
        """Vérifie que la relance 'Qui est-ce ?' ou 'C'est qui ?' après une première question résout l'élève en session."""
        if not self.eleve or not self.admin:
            self.skipTest("Données insuffisantes")

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess["ai_last_eleve_id"] = self.eleve.id
            sess["ai_last_eleve_nom"] = f"{self.eleve.prenom} {self.eleve.nom}"

        resp = self.client.post("/api/assistant/query-data", json={
            "question": "Qui est-ce ?"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["intention"], "fiche_eleve")
        self.assertTrue(data["donnees_trouvees"] > 0)
        self.assertIn(f"[Voir son dossier complet](/eleve/{self.eleve.id})", data["reply"])

    def test_11_bare_name_input_recognition(self):
        """Vérifie que taper simplement le prénom de l'élève (ex: 'Omar') est immédiatement reconnu."""
        if not self.eleve or not self.admin:
            self.skipTest("Données insuffisantes")

        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin.id)
            sess.pop("ai_last_eleve_id", None)

        resp = self.client.post("/api/assistant/query-data", json={
            "question": self.eleve.prenom
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["intention"], "fiche_eleve")
        self.assertTrue(data["donnees_trouvees"] > 0)
        self.assertIn(f"[Voir son dossier complet](/eleve/{self.eleve.id})", data["reply"])


if __name__ == "__main__":
    unittest.main()


