"""
ai_service.py
=============
Service d'intelligence artificielle locale pour KLASORA.
Connecté au moteur local Ollama (modèle qwen2.5:1.5b) sur http://127.0.0.1:11434.
Supporte le multi-tours contextuel et l'extraction d'intention scolaire.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

# Configuration de connexion Ollama
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "30"))

# Prompts Système standards adaptés à KLASORA
SYSTEM_ASSISTANT = (
    "Tu es l'assistant de direction de KLASORA, logiciel de gestion pour "
    "établissements scolaires. Tu réponds aux directeurs avec concision, clarté, rigueur et "
    "toujours en français soigné. Sois direct, professionnel et orienté gestion : ne mentionne jamais de détails "
    "techniques internes, de serveurs ou d'architecture informatique. Ne fabrique aucune donnée imaginaire. Le logiciel KLASORA gère "
    "les absences (justifiées ou non), les notes, les moyennes, les inscriptions par classe et la scolarité. "
    "Il ne gère pas les retards."
)

SYSTEM_INTENT_EXTRACTOR = (
    "Tu es un parseur d'intention pour base de données scolaire de KLASORA. "
    "Analyse la question du directeur et renvoie STRICTEMENT un objet JSON (sans markdown, sans commentaire) avec ce format :\n"
    '{"intention": "fiche_eleve" | "absences" | "notes" | "contact_parent" | "autre", '
    '"classe": string ou null, "eleve": string ou null, "periode": string ou null, "seuil": integer ou null}\n'
    "Règles d'intention :\n"
    "- 'fiche_eleve' : demande de dossier ou informations complètes d'un élève, ou demande combinée de ses notes et de ses absences.\n"
    "- 'notes' : question sur les notes, évaluations ou moyennes d'une classe ou d'un élève.\n"
    "- 'absences' : question sur les absences (élèves absents, nombre d'absences).\n"
    "- 'contact_parent' : recherche du numéro ou email du parent/tuteur d'un élève.\n"
    "- 'autre' : rédaction de document/convocation, conseil administratif ou question générale sans consultation de données."
)


def query_assistant(
    user_prompt: str,
    system_context: str = SYSTEM_ASSISTANT,
    temperature: float = 0.2,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Interroge le moteur IA local Ollama.
    Si un historique court est fourni, utilise l'endpoint multi-tours /api/chat.
    Sinon, utilise l'endpoint direct /api/generate.
    """
    # 1. Utilisation de /api/chat si un historique multi-tours est présent
    if history and isinstance(history, list) and len(history) > 0:
        messages = [{"role": "system", "content": system_context}]
        for item in history[-4:]:
            role = "assistant" if item.get("role") in ("assistant", "bot") else "user"
            content = (item.get("content") or item.get("text") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_prompt})

        chat_url = f"{OLLAMA_BASE_URL}/api/chat"
        chat_payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }
        try:
            response = requests.post(chat_url, json=chat_payload, timeout=OLLAMA_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            reply = data.get("message", {}).get("content", "").strip()
            if reply:
                return reply
        except requests.exceptions.ConnectionError:
            logger.error("Impossible de joindre le serveur Ollama sur %s", chat_url)
            return "Erreur : le service d'intelligence artificielle local (Ollama) est indisponible ou non démarré sur http://127.0.0.1:11434."
        except requests.exceptions.Timeout:
            logger.error("Délai d'attente dépassé lors de l'appel à Ollama (%s secondes)", OLLAMA_TIMEOUT)
            return "Erreur : le traitement IA a pris trop de temps (délai dépassé)."
        except Exception as exc:
            logger.warning("Échec de l'appel /api/chat (%s), bascule sur /api/generate", exc)

    # 2. Appel standard /api/generate (requête unique ou fallback)
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": user_prompt,
        "system": system_context,
        "stream": False,
        "options": {
            "temperature": temperature
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()
    except requests.exceptions.ConnectionError:
        logger.error("Impossible de joindre le serveur Ollama sur %s", url)
        return "Erreur : le service d'intelligence artificielle local (Ollama) est indisponible ou non démarré sur http://127.0.0.1:11434."
    except requests.exceptions.Timeout:
        logger.error("Délai d'attente dépassé lors de l'appel à Ollama (%s secondes)", OLLAMA_TIMEOUT)
        return "Erreur : le traitement IA a pris trop de temps (délai dépassé)."
    except Exception as exc:
        logger.error("Erreur inattendue lors de la requête Ollama : %s", exc)
        return f"Erreur lors du traitement de la requête IA : {exc}"


def chat_with_assistant(user_message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Pour les discussions générales et conseils administratifs avec le directeur.
    Prend en compte l'historique de conversation si disponible.
    """
    return query_assistant(user_message, system_context=SYSTEM_ASSISTANT, temperature=0.7, history=history)


def extract_query_intent(user_message: str) -> Dict[str, Any]:
    """
    Interroge Ollama avec SYSTEM_INTENT_EXTRACTOR, parse le JSON en dictionnaire Python
    de façon sécurisée (avec fallback si le JSON est mal formé).
    """
    default_intent: Dict[str, Any] = {
        "intention": "autre",
        "classe": None,
        "eleve": None,
        "periode": None,
        "seuil": None
    }

    raw_response = query_assistant(user_message, system_context=SYSTEM_INTENT_EXTRACTOR, temperature=0.0)

    if not raw_response or raw_response.startswith("Erreur :"):
        logger.warning("Réponse vide ou erreur Ollama pour l'extraction d'intention : %s", raw_response)
        return default_intent

    # Nettoyage des balises markdown ```json ... ```
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_response.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    # Recherche du bloc JSON {...}
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    json_candidate = match.group(0) if match else cleaned

    try:
        parsed = json.loads(json_candidate)
        if isinstance(parsed, dict):
            intention = str(parsed.get("intention", "autre")).lower().strip()
            if intention not in ("fiche_eleve", "absences", "notes", "contact_parent", "autre"):
                intention = "autre"

            seuil_val = parsed.get("seuil")
            seuil_int: Optional[int] = None
            if seuil_val is not None:
                try:
                    seuil_int = int(seuil_val)
                except (ValueError, TypeError):
                    seuil_int = None

            classe_val = parsed.get("classe")
            eleve_val = parsed.get("eleve")
            periode_val = parsed.get("periode")

            return {
                "intention": intention,
                "classe": classe_val.strip() if isinstance(classe_val, str) and classe_val.strip() else None,
                "eleve": eleve_val.strip() if isinstance(eleve_val, str) and eleve_val.strip() else None,
                "periode": periode_val.strip() if isinstance(periode_val, str) and periode_val.strip() else None,
                "seuil": seuil_int,
            }
    except Exception as exc:
        logger.warning("Échec du parsing JSON pour l'intention ('%s') : %s", raw_response, exc)

    return default_intent
