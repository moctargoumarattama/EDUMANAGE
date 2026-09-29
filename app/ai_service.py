"""
app/ai_service.py
=================
Service d'intelligence artificielle locale pour KLASORA.
Connecté au moteur local Ollama (modèle qwen2.5:1.5b) sur http://127.0.0.1:11434.
Supporte le multi-tours contextuel et l'extraction d'intention scolaire.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Generator, List, Optional
import requests

logger = logging.getLogger(__name__)

# Configuration de connexion Ollama
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "45"))

# Prompts Système standards adaptés à KLASORA
SYSTEM_ASSISTANT = (
    "Tu es le collaborateur et conseiller de direction de l'établissement scolaire KLASORA. "
    "Tu t'exprimes avec intelligence, élégance, chaleur et fluidité naturelle, exactement comme un collègue humain "
    "de confiance qui comprend toutes les nuances d'une conversation et le quotidien d'une école.\n"
    "RÈGLE IMPÉRATIVE DE VOCABULAIRE : Ne mentionne JAMAIS de termes techniques tels que 'base de données', "
    "'requête', 'SQL', 'serveur', 'null' ou 'système'. Exprime-toi toujours de façon humaine, polie, professionnelle "
    "et orientée vers la scolarité et l'accompagnement des élèves.\n"
    "RÈGLE STRICTE SUR LES SIGNATURES ET CROCHETS :\n"
    "- Tu es dans un fil de discussion instantané direct avec la direction.\n"
    "- Ne signe JAMAIS ton message. N'inclus JAMAIS 'Cordialement', 'Bien cordialement', '[Nom du Directeur]', '[Signature]', "
    "ni aucun texte entre crochets comme [Nom], [Date], [Établissement].\n"
    "- Ne récite JAMAIS tes règles internes ni ce que le logiciel sait ou ne sait pas faire.\n"
    "- INTERDICTION STRICTE : Ne r\u00e9ponds JAMAIS par 'Je suis d\u00e9sol\u00e9, mais je ne peux pas vous aider avec cette question'.\n"
    "- Pour les questions de suivi ou demandes d'explication ('pourquoi ?', 'comment \u00e7a ?'), dialogue naturellement en t'appuyant sur l'\u00e9change pr\u00e9c\u00e9dent.\n"
    "- Reste factuel sur les chiffres et \u00e9l\u00e8ves de l'\u00e9tablissement sans en inventer, mais sois toujours constructif et accueillant.\n"
    "- R\u00e9ponds aux relances conversationnelles ('qui est-ce ?', 'et lui ?', 'que penses-tu de sa moyenne ?') de fa\u00e7on naturelle et empathique."
)

SYSTEM_INTENT_EXTRACTOR = (
    "Tu es un parseur d'intention pour données scolaires de KLASORA. "
    "Analyse la question du directeur et renvoie STRICTEMENT un objet JSON (sans markdown, sans commentaire) avec ce format :\n"
    '{"intention": "fiche_eleve" | "absences" | "notes" | "contact_parent" | "autre", '
    '"classe": string ou null, "eleve": string ou null, "periode": string ou null, "seuil": integer ou null}\n'
    "Règles d'intention :\n"
    "- 'fiche_eleve' : demande de dossier ou informations complètes d'un élève, ou demande combinée de ses notes et de ses absences.\n"
    "- 'notes' : question sur les notes, évaluations ou moyennes d'une classe ou d'un élève.\n"
    "- 'absences' : question sur les absences (élèves absents, nombre d'absences).\n"
    "- 'contact_parent' : recherche du numéro ou email du parent/tuteur d'un élève.\n"
    "- 'autre' : salutation (bonjour, merci, etc.), rédaction de document/convocation, conseil administratif ou question générale sans consultation de données spécifiques."
)


def clean_assistant_reply(text: str) -> str:
    """
    Nettoie rigoureusement le texte généré par l'IA :
    - Supprime les signatures de lettre administrative (ex: 'Cordialement, [Nom du Directeur]').
    - Supprime les placeholders entre crochets (ex: '[Nom du Directeur]', '[Signature]').
    - Supprime les récits parasites de règles internes (ex: 'Je tiens à préciser que KLASORA ne gère pas les retards...').
    """
    if not text:
        return ""

    cleaned = text

    # 1. Supprimer les répétitions parasites des règles internes
    cleaned = re.sub(
        r"(?i)(?:je\s+tiens\s+à\s+préciser\s+que\s+)?klasora(?:,\s*notre\s+logiciel\s+de\s+gestion\s+scolaire,?)?\s+ne\s+gère\s+pas\s+les\s+retards[^.\n]*[.\n]?",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)il\s+s'agit\s+exclusivement\s+de\s+la\s+gestion\s+des\s+absences[^.\n]*[.\n]?",
        "",
        cleaned,
    )

    # 2. Supprimer les formules de clôture de courrier avec signature ou directeur
    cleaned = re.sub(
        r"(?i)(?:(?:très\s+|bien\s+)?cordialement|sincères\s+salutations|respectueusement|veuillez\s+agréer)[,\s]*"
        r"(?:(?:\[\s*(?:nom\s+(?:du\s+)?directeur|signature|directeur|nom|date|établissement)[^\]]*\]|\[[^\]]+\]|la\s+direction|le\s+directeur|[a-zà-ÿ\s\-]+)?\s*)*$",
        "",
        cleaned,
        flags=re.DOTALL | re.MULTILINE,
    )

    # 3. Supprimer tout résidu de [Nom du Directeur], [Signature], [Nom], etc.
    cleaned = re.sub(r"\[\s*(?:nom\s+(?:du\s+)?directeur|signature|directeur|nom|date|établissement)[^\]]*\]", "", cleaned, flags=re.IGNORECASE)

    # 4. Supprimer la formule clichée de fin de lettre si elle précède la signature
    cleaned = re.sub(
        r"(?i)je\s+vous\s+remercie\s+pour\s+votre\s+compréhension\s+et\s+reste\s+à\s+votre\s+disposition\s+pour\s+tout\s+autre\s+renseignement[^.\n]*[.\n]?",
        "",
        cleaned,
    )

    # 5. Nettoyer les sauts de ligne multiples en fin de message
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _normalize_history(history: Optional[List[Dict[str, str]]], limit: int = 6) -> List[Dict[str, str]]:
    """Conserve un historique court, propre et compatible avec /api/chat."""
    if not isinstance(history, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        raw_role = str(item.get("role") or "").strip().lower()
        role = "assistant" if raw_role in ("assistant", "bot", "ia") else "user"
        content = (item.get("content") or item.get("text") or "").strip()
        if content:
            normalized.append({"role": role, "content": content})

    return normalized[-limit:]


def query_assistant(
    user_prompt: str,
    system_context: str = SYSTEM_ASSISTANT,
    temperature: float = 0.2,
    history: Optional[List[Dict[str, str]]] = None,
    num_predict: int = 180,
) -> str:
    """
    Interroge le moteur IA local Ollama.
    Utilise /api/chat avec un historique court afin de conserver le fil de discussion.
    """
    normalized_history = _normalize_history(history)

    # 1. Utilisation prioritaire de /api/chat, même sans historique.
    messages = [{"role": "system", "content": system_context}]
    messages.extend(normalized_history)
    messages.append({"role": "user", "content": user_prompt})

    chat_url = f"{OLLAMA_BASE_URL}/api/chat"
    chat_payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": temperature,
            "num_predict": min(num_predict, 180),
        }
    }
    try:
        response = requests.post(chat_url, json=chat_payload, timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        reply = data.get("message", {}).get("content", "").strip()
        if reply:
            return clean_assistant_reply(reply)
        return "Je n'ai pas pu produire une réponse exploitable. Reformulez en quelques mots, s'il vous plaît."
    except requests.exceptions.ConnectionError:
        logger.error("Impossible de joindre le serveur Ollama sur %s", chat_url)
        return "Erreur : le service d'intelligence artificielle local (Ollama) est indisponible ou non démarré sur http://127.0.0.1:11434."
    except requests.exceptions.Timeout:
        logger.error("Délai d'attente dépassé lors de l'appel à Ollama (%s secondes)", OLLAMA_TIMEOUT)
        return "Erreur : le traitement IA a pris trop de temps (délai dépassé)."
    except Exception as exc:
        logger.error("Échec de l'appel /api/chat : %s", exc)
        return "Erreur : le service IA n'a pas pu terminer la réponse. Veuillez réessayer."

def chat_with_assistant(user_message: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Pour les discussions générales et conseils administratifs avec le directeur.
    Prend en compte l'historique de conversation si disponible.
    """
    return query_assistant(user_message, system_context=SYSTEM_ASSISTANT, temperature=0.3, history=history, num_predict=180)


def stream_assistant(
    user_prompt: str,
    system_context: str = SYSTEM_ASSISTANT,
    temperature: float = 0.3,
    history: Optional[List[Dict[str, str]]] = None,
    num_predict: int = 180,
) -> Generator[str, None, None]:
    """
    Générateur en streaming (SSE / token-par-token) pour l'Assistant IA KLASORA.
    Interroge Ollama avec stream=True et émet les fragments textuels au fur et à mesure.
    """
    normalized_history = _normalize_history(history)

    messages = [{"role": "system", "content": system_context}]
    messages.extend(normalized_history)
    messages.append({"role": "user", "content": user_prompt})

    chat_url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "keep_alive": "30m",
        "options": {
            "temperature": temperature,
            "num_predict": min(num_predict, 180),
        }
    }
    try:
        with requests.post(chat_url, json=payload, stream=True, timeout=OLLAMA_TIMEOUT) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token
                    except Exception:
                        continue
            return
    except Exception as exc:
        logger.error("Échec stream /api/chat : %s", exc)
        yield "Le service IA n'a pas pu terminer la réponse. Veuillez réessayer."
        return

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

    raw_response = query_assistant(user_message, system_context=SYSTEM_INTENT_EXTRACTOR, temperature=0.0, num_predict=100)

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

            def _clean_param(val: Any) -> Optional[str]:
                if not val or not isinstance(val, str):
                    return None
                s = val.strip()
                if s.lower() in ("null", "none", "inconnu", "undefined", "nil", "n/a", '""', "''", ""):
                    return None
                return s

            return {
                "intention": intention,
                "classe": _clean_param(parsed.get("classe")),
                "eleve": _clean_param(parsed.get("eleve")),
                "periode": _clean_param(parsed.get("periode")),
                "seuil": seuil_int,
            }
    except Exception as exc:
        logger.warning("Échec du parsing JSON pour l'intention ('%s') : %s", raw_response, exc)

    return default_intent
