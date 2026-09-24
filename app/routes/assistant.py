"""
app/routes/assistant.py
=======================
Blueprint API pour l'Assistant de Direction IA de KLASORA.
Routes sous le préfixe /api/assistant.
Sécurisé pour les utilisateurs authentifiés avec le rôle 'directeur', 'admin' ou 'super_admin'.

Fonctionnalités avancées intégrées :
1. Recherche tolérante aux fautes d'orthographe (Fuzzy Matching & normalisation NFD sans accents).
2. Mémoire d'entité en session Flask (résolution des anaphores : "ses notes", "son absence", etc.).
3. Mémoire conversationnelle multi-tours courte via Ollama (/api/chat).
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime
import difflib
import logging
import re
from typing import Any, Dict, List, Optional
import unicodedata

from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from app import db
from app.ai_service import (
    SYSTEM_ASSISTANT,
    chat_with_assistant,
    clean_assistant_reply,
    extract_query_intent,
    query_assistant,
)
from app.authorization import role_required
from app.models import Absence, AnneeScolaire, Classe, Cours, Eleve, Inscription, Note, Utilisateur
from app.services.annees_scolaires import get_annee_active

logger = logging.getLogger(__name__)

assistant_bp = Blueprint("assistant", __name__, url_prefix="/api/assistant")

# Expressions régulières pour détecter les références anaphoriques (pronoms possessifs, relatifs et questions de suivi)
ANAPHORA_PATTERNS = [
    r"\bses\b", r"\bson\b", r"\bsa\b", r"\blui\b", r"\bcet\s+élève\b", r"\bcet\s+eleve\b",
    r"\bce\s+dernier\b", r"\bpour\s+lui\b", r"\bpour\s+elle\b", r"\bl['’]élève\b", r"\bl['’]eleve\b",
    r"\bet\s+ses\b", r"\bet\s+les\s+absences\b", r"\bet\s+les\s+notes\b", r"\bson\s+absence\b",
    r"\bsa\s+note\b", r"\bses\s+notes\b", r"\bcontacte-le\b", r"\bcontacte\s+le\b",
    r"\bappelle-le\b", r"\bjoindre\b", r"\bcontacter\b",
    r"\bqui\s+est-ce\b", r"\bqui\s+est\s+ce\b", r"\bc['’]est\s+qui\b", r"\bqui\s+c['’]est\b",
    r"\bparle-moi\s+de\s+lui\b", r"\bparle\s+moi\s+de\s+lui\b", r"\bde\s+qui\b",
    r"\bet\s+lui\b", r"\bet\s+elle\b", r"\bdis-moi\s+en\s+plus\b", r"\bplus\s+d['’]infos?\b"
]


def _normalize_text(text: str) -> str:
    """
    Normalise le texte :
    - Décompose les accents (forme NFD) et élimine les marques diacritiques.
    - Met en minuscules.
    - Remplace les caractères spéciaux par des espaces.
    - Supprime les espaces multiples.
    Exemple : "Hélène" -> "helene", "Mamadou" -> "mamadou".
    """
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", ascii_text).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


# Mots-clés et salutations pour l'interception directe chitchat
SALUTATIONS = {
    "bonjour", "bonsoir", "salut", "coucou", "hello", "hi",
    "merci", "merci beaucoup", "au revoir", "a bientot", "à bientôt",
    "aide", "aide-moi", "aide moi", "qui es-tu", "qui es tu", "test"
}

SCHOOL_KEYWORDS = {
    "classe", "classes", "eleve", "eleves", "élève", "élèves",
    "note", "notes", "absence", "absences", "moyenne", "moyennes",
    "bulletin", "bulletins", "parent", "parents", "cours", "matiere", "matières", "matieres",
    "dossier", "fiche", "trimestre", "semestre", "scolarite", "scolarité",
    "frais", "paiement", "paiements", "solde"
}


def _get_chitchat_response(text: str) -> Optional[str]:
    """
    Interception prioritaire des salutations et formules de politesse :
    Si la question ne contient aucun terme scolaire et correspond à une salutation,
    répondre immédiatement sans appel SQL ni appel IA.
    """
    if not text:
        return None
    raw = text.lower().strip()
    cleaned = re.sub(r"[^\w\s]", " ", raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    tokens = set(cleaned.split())

    # S'il y a un mot-clé scolaire précis, laisser le moteur de données traiter la demande
    if tokens & SCHOOL_KEYWORDS:
        return None

    # 1. Correspondance directe
    is_greeting = cleaned in SALUTATIONS or raw in SALUTATIONS

    # 2. Commence par une formule de politesse courte sans mention de classe ni de matière
    if not is_greeting:
        for sal in SALUTATIONS:
            if cleaned == sal or cleaned.startswith(sal + " "):
                if len(tokens) <= 6:
                    is_greeting = True
                    break

    if not is_greeting:
        return None

    if any(m in cleaned for m in ("merci", "merci beaucoup", "remercie")):
        return (
            "Je vous en prie ! Je reste à votre entière disposition pour tout renseignement "
            "sur vos élèves ou votre établissement."
        )
    if any(m in cleaned for m in ("au revoir", "a bientot", "à bientôt", "adieu")):
        return "Au revoir Monsieur/Madame la Direction. Excellente journée à vous et à très bientôt sur KLASORA !"

    return (
        "Bonjour Monsieur/Madame la Direction. Je suis votre assistant KLASORA. "
        "Comment puis-je vous aider aujourd'hui ? Vous pouvez me demander la fiche d'un élève, "
        "un bilan d'absences, des moyennes de classe ou la rédaction d'un document administratif."
    )


def _clean_search_term(term: str) -> str:
    """Nettoie les stop-words et préfixes fréquents d'une question scolaire ou conversationnelle."""
    if not term:
        return ""
    cleaned = re.sub(
        r"^(?:qui\s+est\s+(?:l['’]eleve\s+|l['’]élève\s+|ce\s+|cet\s+|cette\s+)?|qui\s+est|c['’]est\s+qui|connais-tu|tu\s+connais|parle-moi\s+de|parlez-moi\s+de|qu['’]en\s+est-il\s+de|dis-moi\s+sur|infos?\s+sur|renseigne-moi\s+sur|l['’]eleve|l['’]élève|eleve|élève|de|d['’]|fiche|dossier|bulletin|notes?|absences?|pour|sur|donne-moi|donne\s+moi)\s+",
        "",
        term.strip(),
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"[?!.,;:]+$", "", cleaned).strip()
    if cleaned.lower() in ("null", "none", "inconnu", "undefined", "nil", "n/a", "ce", "cet", "cette", "lui", ""):
        return ""
    return cleaned


try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


def _calculate_eleve_similarity(norm_term: str, el: Eleve) -> float:
    """
    Calcule le score de similarité (0.0 à 1.0) entre un terme de recherche normalisé
    et les attributs d'un élève.
    Utilise rapidfuzz (C++) si présent dans l'environnement, sinon difflib natif Python (zéro dépendance).
    """
    p = _normalize_text(el.prenom)
    n = _normalize_text(el.nom)
    full = f"{p} {n}".strip()
    rev = f"{n} {p}".strip()

    if HAS_RAPIDFUZZ:
        s_token = fuzz.token_sort_ratio(norm_term, full) / 100.0
        s_full = fuzz.ratio(norm_term, full) / 100.0
        s_rev = fuzz.ratio(norm_term, rev) / 100.0
        s_nom = fuzz.ratio(norm_term, n) / 100.0
        s_prenom = fuzz.ratio(norm_term, p) / 100.0
        return max(s_token, s_full, s_rev, s_nom, s_prenom)

    # Fallback difflib natif standard (inclus de base dans Python)
    r_full = difflib.SequenceMatcher(None, norm_term, full).ratio()
    r_rev = difflib.SequenceMatcher(None, norm_term, rev).ratio()
    r_nom = difflib.SequenceMatcher(None, norm_term, n).ratio()
    r_prenom = difflib.SequenceMatcher(None, norm_term, p).ratio()

    # Découpage multi-mots
    parts = norm_term.split()
    r_tokens = 0.0
    if len(parts) >= 2:
        w1, w2 = parts[0], " ".join(parts[1:])
        s1 = (difflib.SequenceMatcher(None, w1, p).ratio() + difflib.SequenceMatcher(None, w2, n).ratio()) / 2.0
        s2 = (difflib.SequenceMatcher(None, w1, n).ratio() + difflib.SequenceMatcher(None, w2, p).ratio()) / 2.0
        r_tokens = max(s1, s2)

    return max(r_full, r_rev, r_nom, r_prenom, r_tokens)


def _search_eleves_in_ecole(search_term: str, ecole_id: int) -> List[Eleve]:
    """
    Recherche les élèves correspondants dans l'établissement scolaire courant :
    1. Recherche directe SQL paramétrée (rapide, exacte ou partielle).
    2. Fallback Fuzzy Matching tolérant aux fautes d'orthographe et aux accents (score >= 0.75).
    """
    if not search_term or not ecole_id:
        return []

    clean_term = _clean_search_term(search_term)
    if not clean_term:
        return []

    base_query = (
        Eleve.query.options(
            joinedload(Eleve.notes).joinedload(Note.cours),
            joinedload(Eleve.absences).joinedload(Absence.cours),
            joinedload(Eleve.paiements),
            joinedload(Eleve.parent),
            joinedload(Eleve.inscriptions).joinedload(Inscription.classe),
        )
        .filter(Eleve.ecole_id == ecole_id)
    )

    # 1. Tentative SQL directe avec ilike
    parts = clean_term.split()
    sql_results: List[Eleve] = []

    if len(parts) == 1:
        pattern = f"%{parts[0]}%"
        sql_results = base_query.filter(
            or_(
                Eleve.nom.ilike(pattern),
                Eleve.prenom.ilike(pattern),
                Eleve.code_parent.ilike(pattern),
            )
        ).limit(5).all()
    else:
        p1, p2 = parts[0], " ".join(parts[1:])
        sql_results = base_query.filter(
            or_(
                and_(Eleve.prenom.ilike(f"%{p1}%"), Eleve.nom.ilike(f"%{p2}%")),
                and_(Eleve.nom.ilike(f"%{p1}%"), Eleve.prenom.ilike(f"%{p2}%")),
                Eleve.nom.ilike(f"%{clean_term}%"),
                Eleve.prenom.ilike(f"%{clean_term}%"),
            )
        ).limit(5).all()

    if sql_results:
        return sql_results

    # 2. Fallback Fuzzy Matching & Normalisation des accents (score >= 0.75)
    norm_term = _normalize_text(clean_term)
    if not norm_term or len(norm_term) < 2:
        return []

    all_eleves = base_query.all()
    scored_candidates = []

    for el in all_eleves:
        score = _calculate_eleve_similarity(norm_term, el)
        if score >= 0.75:
            scored_candidates.append((score, el))

    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    if scored_candidates:
        best_score, best_eleve = scored_candidates[0]
        logger.info(
            "Fuzzy matching réussi pour '%s' -> '%s %s' (score: %.3f)",
            search_term,
            best_eleve.prenom,
            best_eleve.nom,
            best_score
        )
        return [c[1] for c in scored_candidates[:3]]

    return []


def _build_eleve_dossier(eleve: Eleve, ecole_id: int, annee_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Construit un dossier scolaire complet et structuré pour un élève :
    - Identité et matricule
    - Classe et inscription active
    - Coordonnées des parents / tuteurs
    - Absences (total, justifiées, non justifiées, détails récents)
    - Notes, matières, coefficients et moyenne générale
    - Frais de scolarité et paiements
    """
    inscriptions = [i for i in eleve.inscriptions if i.ecole_id == ecole_id]
    if annee_id:
        inscr_active = next((i for i in inscriptions if i.annee_scolaire_id == annee_id), None)
    else:
        inscr_active = inscriptions[-1] if inscriptions else None

    classe_nom = inscr_active.classe.nom if (inscr_active and inscr_active.classe) else "Non assignée"

    if eleve.parent:
        p_prenom = (eleve.parent.prenom or "").strip()
        p_nom = (eleve.parent.nom or "").strip()
        parent_nom = f"{p_prenom} {p_nom}".strip() or None
    else:
        parent_nom = None
    parent_tel = eleve.contact_parent or (eleve.parent.telephone if eleve.parent else None) or "Non renseigné"
    parent_email = eleve.email_parent or (eleve.parent.email if eleve.parent else None) or "Non renseigné"

    all_absences = eleve.absences or []
    if annee_id and inscr_active:
        absences = [a for a in all_absences if a.inscription_id == inscr_active.id or a.ecole_id == ecole_id]
    else:
        absences = all_absences

    total_abs = len(absences)
    justifiees = sum(1 for a in absences if a.justifiee)
    non_justifiees = total_abs - justifiees

    recent_absences = sorted(absences, key=lambda a: a.date_absence or dt.date.min, reverse=True)[:5]
    recent_abs_list = []
    for a in recent_absences:
        d_str = a.date_absence.strftime("%d/%m/%Y") if a.date_absence else "Date inconnue"
        st = "Justifiée" if a.justifiee else "Non justifiée"
        c_str = f" en {a.cours.nom}" if getattr(a, "cours", None) else ""
        motif_str = f" - Motif: {a.motif}" if a.motif else ""
        recent_abs_list.append(f"{d_str} [{st}]{c_str}{motif_str}")

    all_notes = eleve.notes or []
    if annee_id:
        notes = [n for n in all_notes if n.annee_id == annee_id]
    else:
        notes = all_notes

    notes_sorted = sorted(notes, key=lambda n: n.date_evaluation or datetime.min, reverse=True)
    notes_list_str = []
    for n in notes_sorted[:15]:
        c_nom = n.cours.nom if getattr(n, "cours", None) else "Matière"
        coef = f" (coef {n.coefficient})" if n.coefficient and n.coefficient != 1.0 else ""
        per = f" [{n.periode}]" if n.periode else ""
        notes_list_str.append(f"{c_nom} : {n.valeur}/20{coef}{per}")

    moyenne = eleve.moyenne_generale()

    frais = eleve.frais_annuels or 0.0
    paye = eleve.total_paye()
    reste = eleve.reste_a_payer()

    return {
        "eleve_id": eleve.id,
        "nom_complet": f"{eleve.prenom} {eleve.nom}",
        "matricule": eleve.code_parent or f"ELV-{eleve.id}",
        "classe": classe_nom,
        "statut": eleve.statut or "actif",
        "genre": eleve.genre or "M",
        "date_naissance": eleve.date_naissance.strftime("%d/%m/%Y") if eleve.date_naissance else "Non renseignée",
        "lieu_naissance": eleve.lieu_naissance or "Non renseigné",
        "adresse": eleve.adresse or "Non renseignée",
        "parent_nom": parent_nom,
        "parent_tel": parent_tel,
        "parent_email": parent_email,
        "total_absences": total_abs,
        "absences_justifiees": justifiees,
        "absences_non_justifiees": non_justifiees,
        "absences_recentes": recent_abs_list,
        "nombre_notes": len(notes),
        "notes_details": notes_list_str,
        "moyenne_generale": moyenne,
        "frais_annuels": frais,
        "total_paye": paye,
        "reste_a_payer": reste,
    }


def _format_fiche_eleve_markdown(d: Dict[str, Any]) -> str:
    """
    Formatte le profil d'un élève de façon humaine, élégante et synthétique,
    accompagné du bouton interactif d'accès à son dossier complet.
    """
    el_id = d.get("eleve_id")
    nom = d.get("nom_complet", "Élève")
    classe = d.get("classe", "Non assignée")
    matricule = d.get("matricule", "-")
    statut = (d.get("statut") or "actif").capitalize()

    # Synthèse académique humaine
    moyenne = d.get("moyenne_generale")
    nb_notes = d.get("nombre_notes", 0)
    notes_details = d.get("notes_details") or []
    if moyenne is not None:
        if moyenne >= 16:
            appreciation = f"Excellent niveau académique avec une moyenne de **{moyenne}/20** ({nb_notes} évaluation(s))"
        elif moyenne >= 14:
            appreciation = f"Très bon travail avec une moyenne générale de **{moyenne}/20** ({nb_notes} évaluation(s))"
        elif moyenne >= 12:
            appreciation = f"Bon niveau d'ensemble avec une moyenne de **{moyenne}/20** ({nb_notes} évaluation(s))"
        elif moyenne >= 10:
            appreciation = f"Moyenne générale de **{moyenne}/20** ({nb_notes} évaluation(s))"
        else:
            appreciation = f"Moyenne de **{moyenne}/20** ({nb_notes} évaluation(s), suivi pédagogique recommandé)"
    else:
        appreciation = "Aucune note enregistrée pour le moment" if nb_notes == 0 else f"{nb_notes} note(s) au dossier"

    # Assiduité & absences
    total_abs = d.get("total_absences", 0)
    just = d.get("absences_justifiees", 0)
    non_just = d.get("absences_non_justifiees", 0)
    if total_abs == 0:
        abs_text = "Exemplaire (aucune absence signalée)"
    else:
        details_abs = []
        if non_just > 0:
            details_abs.append(f"**{non_just} non justifiée(s)**")
        if just > 0:
            details_abs.append(f"{just} justifiée(s)")
        abs_text = f"**{total_abs} absence(s)** au total ({', '.join(details_abs)})"

    # Situation financière
    try:
        frais = int(d.get("frais_annuels") or 0)
        paye = int(d.get("total_paye") or 0)
        reste = int(d.get("reste_a_payer") or 0)
    except Exception:
        frais, paye, reste = 0, 0, 0

    if reste <= 0:
        finances_text = "Scolarité à jour (aucun arriéré)"
    else:
        reste_fmt = f"{reste:,} FCFA".replace(",", " ")
        frais_fmt = f"{frais:,} FCFA".replace(",", " ")
        finances_text = f"Reste dû : **{reste_fmt}** (sur {frais_fmt})"

    # Contact responsable
    parent = d.get("parent_nom")
    tel = d.get("parent_tel")
    contact_parts = []
    if parent:
        contact_parts.append(f"**{parent}**")
    if tel and tel != "Non renseigné":
        contact_parts.append(f"`{tel}`")
    contact_text = " — ".join(contact_parts) if contact_parts else "Non renseigné"

    link_btn = f"[Voir son dossier complet](/eleve/{el_id})" if el_id else ""

    lines = [
        f"**{nom}** est actuellement élève en classe de **{classe}** (Matricule : `{matricule}`, Statut : {statut}).",
        "",
        "Voici la synthèse de son parcours et de sa situation :",
        f"• 📚 **Performances scolaires :** {appreciation}.",
        f"• ⏱️ **Assiduité :** {abs_text}.",
        f"• 💳 **Frais de scolarité :** {finances_text}.",
        f"• 👤 **Responsable légal :** {contact_text}.",
    ]

    if notes_details:
        lines.append(f"• 📝 **Dernières notes :** {', '.join(notes_details[:3])}")
    if d.get("absences_recentes"):
        lines.append(f"• ⚠️ **Dernier signalement :** {d['absences_recentes'][0]}")

    if link_btn:
        lines.extend([
            "",
            "Pour consulter l'ensemble de ses bulletins, relevés et pièces administratives :",
            link_btn
        ])

    return "\n".join(lines).strip()


def _format_contact_parent_markdown(d: Dict[str, Any]) -> str:
    """Formatte les coordonnées du parent/tuteur de manière claire, humaine et instantanée."""
    el_id = d.get("eleve_id")
    nom = d.get("nom_complet", "Élève")
    classe = d.get("classe", "Non assignée")
    parent = d.get("parent_nom") or "Non renseigné"
    tel = d.get("parent_tel") or d.get("telephone_parent") or "Non renseigné"
    email = d.get("parent_email") or d.get("email_parent") or "Non renseigné"
    adresse = d.get("adresse") or "Non renseignée"

    lines = [
        f"Voici les coordonnées du responsable de **{nom}** ({classe}) :",
        "",
        f"• 👤 **Parent / Tuteur :** {parent}",
        f"• 📞 **Téléphone :** `{tel}`",
        f"• ✉️ **Email :** {email}",
        f"• 📍 **Adresse :** {adresse}"
    ]
    if el_id:
        lines.extend([
            "",
            f"[Voir son dossier complet](/eleve/{el_id})"
        ])
    return "\n".join(lines).strip()


def _fast_detect_intent_and_entities(question: str) -> Optional[Dict[str, Any]]:
    """
    Classification heuristique ultra-rapide (< 0.001s) en pur Python :
    Évite un appel réseau lourd à Ollama si la question correspond clairement
    à l'une des requêtes scolaires courantes ou aux suggestions (pills) de l'interface.
    """
    if not question:
        return None

    raw = question.strip()
    q_norm = _normalize_text(raw)

    # Extraction rapide du seuil s'il existe (ex: "plus de 3 absences", "notes supérieures à 15")
    seuil: Optional[int] = None
    m_seuil = re.search(r"(?:plus\s+de|sup[eé]rieur[es]*\s+[aà]|au\s+moins)\s+(\d+)", raw, re.IGNORECASE)
    if m_seuil:
        try:
            seuil = int(m_seuil.group(1))
        except ValueError:
            seuil = None

    # Extraction rapide de la période (Trimestre 1, Semestre 2, etc.)
    periode: Optional[str] = None
    m_per = re.search(r"\b(trimestre\s+[123]|semestre\s+[12])\b", raw, re.IGNORECASE)
    if m_per:
        periode = m_per.group(1).title()

    # Détection de l'élève par regex
    eleve_nom: Optional[str] = None
    eleve_patterns = [
        r"(?:qui\s+est\s+(?:l['’]élève\s+|l['’]eleve\s+|ce\s+|cet\s+|cette\s+)?|c['’]est\s+qui\s+|connais-tu\s+|tu\s+connais\s+|parle-moi\s+de\s+|parlez-moi\s+de\s+|qu['’]en\s+est-il\s+de\s+|infos?\s+sur\s+|renseigne-moi\s+sur\s+|dis-moi\s+sur\s+)([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
        r"(?:l['’]élève|l['’]eleve|eleve|élève)\s+([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
        r"(?:fiche|dossier|bulletin|notes?|absences?|coordonnées|contact)\s+(?:de\s+|d['’]\s*)?([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
        r"(?:pour|sur|concernant)\s+([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
    ]
    for pat in eleve_patterns:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            cand = _clean_search_term(m.group(1))
            if cand and cand.lower() not in (
                "la", "cette", "tous", "toutes", "chaque", "classe", "ecole", "école",
                "son", "ses", "des", "une", "un", "mon", "notre", "leurs", "lui",
                "null", "none", "inconnu", "undefined", "nil", "n/a", "critiques", "tout", "toutes", "complete", "complète", "qui", "ce"
            ):
                eleve_nom = cand
                break

    # Détection de la classe
    classe_nom: Optional[str] = None
    m_classe = re.search(r"\b(6e|5e|4e|3e|2nde|1ere|terminale|tle|cp1|cp2|ce1|ce2|cm1|cm2)(?:\s+([a-z]))?\b", raw, re.IGNORECASE)
    if m_classe:
        c_base = m_classe.group(1)
        c_sec = m_classe.group(2)
        classe_nom = f"{c_base} {c_sec}".strip().upper() if c_sec else c_base.upper()

    # Mots-clés pour fiche élève
    is_fiche = any(k in q_norm for k in ("fiche", "dossier", "information", "informations", "info", "infos", "profil", "tout savoir", "qui est", "c est qui", "connais tu", "parle moi", "statut de"))

    # Si aucun nom n'est extrait mais que l'utilisateur a tapé uniquement 1 à 3 mots (ex: 'Omar' ou 'Omar Salah')
    if not eleve_nom and not classe_nom:
        words = raw.split()
        if 1 <= len(words) <= 3 and all(w.replace("-", "").isalpha() for w in words):
            cand_clean = _clean_search_term(raw)
            if cand_clean and cand_clean.lower() not in (
                "bonjour", "bonsoir", "salut", "coucou", "hello", "hi", "merci",
                "aide", "test", "classe", "ecole", "note", "notes", "absence", "absences",
                "oui", "non", "null", "none", "inconnu", "undefined", "qui", "quoi", "comment"
            ):
                eleve_nom = cand_clean
                is_fiche = True
    # Mots-clés pour contact
    is_contact = any(k in q_norm for k in ("contact", "contacts", "coordonnee", "coordonnees", "parent", "parents", "telephone", "telephones", "tel", "numero", "numeros", "email", "mail", "joindre", "appeler"))
    # Mots-clés pour absences
    is_absences = any(k in q_norm for k in ("absence", "absences", "absent", "absents", "assiduite"))
    # Mots-clés pour notes
    is_notes = any(k in q_norm for k in ("note", "notes", "moyenne", "moyennes", "bulletin", "bulletins", "evaluation", "evaluations", "resultat", "resultats"))

    # Mots-clés pour pilotage de rentrée scolaire (Chantier D)
    is_rentree_word = any(k in q_norm for k in ("rentree", "rentre"))
    is_statut_rentree = (
        is_rentree_word and any(k in q_norm for k in ("avancement", "preparation", "ou en est", "ou en sommes", "statut", "etat", "wizard", "pilotage", "suivi", "point", "comment se passe"))
    ) or any(k in q_norm for k in ("ou en est la rentree", "etat de la rentree", "statut rentree", "avancement rentree", "preparer la rentree", "wizard rentree"))

    is_relance_reinscriptions = any(k in q_norm for k in (
        "qui n a pas paye", "qui n a pas regle", "pas encore paye", "pas paye",
        "preinscrit", "preinscrits", "attente de reinscription", "attente reinscription",
        "non confirme", "non confirmes", "acompte", "acomptes", "qui doit payer",
        "relance reinscription", "relance reinscriptions", "relances reinscription"
    ))

    # Intentions prioritaires
    if is_relance_reinscriptions:
        return {"intention": "relance_reinscriptions", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil}
    if is_statut_rentree:
        return {"intention": "statut_rentree", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil}
    if is_fiche or (is_notes and is_absences):
        return {"intention": "fiche_eleve", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil}
    if is_contact and not is_notes and not is_absences:
        return {"intention": "contact_parent", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil}
    if is_absences and not is_notes:
        return {"intention": "absences", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil}
    if is_notes and not is_absences:
        return {"intention": "notes", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil}

    # Si rien de net n'est détecté, on laisse la main au parseur Ollama
    return None


@assistant_bp.route("/clear-session", methods=["POST"])
@login_required
@role_required("admin", "directeur", "super_admin")
def api_assistant_clear_session():
    """
    POST /api/assistant/clear-session
    Réinitialise la mémoire d'entité en session Flask (dernier élève et classe mémorisés).
    """
    session.pop("ai_last_eleve_id", None)
    session.pop("ai_last_eleve_nom", None)
    session.pop("ai_last_classe", None)
    session.modified = True
    return jsonify({
        "success": True,
        "message": "Mémoire de session réinitialisée."
    })


@assistant_bp.route("/chat", methods=["POST"])
@login_required
@role_required("admin", "directeur", "super_admin")
def api_assistant_chat():
    """
    POST /api/assistant/chat
    Reçoit {"message": "...", "history": [...]} et renvoie la réponse de l'assistant.
    """
    payload = request.get_json(silent=True) or {}
    user_message = (payload.get("message") or payload.get("prompt") or "").strip()
    history = payload.get("history") or []

    if not user_message:
        return jsonify({
            "success": False,
            "error": "Le champ 'message' est requis."
        }), 400

    # 1. Interception prioritaire des salutations (Chitchat direct sans IA)
    chitchat = _get_chitchat_response(user_message)
    if chitchat:
        return jsonify({
            "success": True,
            "reply": chitchat
        })

    try:
        reply = chat_with_assistant(user_message, history=history)
        return jsonify({
            "success": True,
            "reply": reply
        })
    except Exception as exc:
        logger.error("Erreur dans /api/assistant/chat : %s", exc)
        return jsonify({
            "success": False,
            "error": f"Erreur interne du service assistant : {exc}"
        }), 500


@assistant_bp.route("/query-data", methods=["POST"])
@login_required
@role_required("admin", "directeur", "super_admin")
def api_assistant_query_data():
    """
    POST /api/assistant/query-data
    Reçoit une question en langage naturel sur les données scolaires de KLASORA :
    - Fiche complète d'un élève (notes, moyenne, absences, scolarité, contacts)
    - Notes et moyenne d'un élève ou d'une classe
    - Absences (justifiées ou non) d'un élève ou d'une classe
    - Contacts parents / tuteurs
    - Conseils administratifs et rédaction officielle
    Prend en compte la session Flask (anaphores) et l'historique court multi-tours.
    """
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or payload.get("message") or "").strip()
    history = payload.get("history") or []

    if not question:
        return jsonify({
            "success": False,
            "error": "Le champ 'question' est requis."
        }), 400

    # 1. Interception prioritaire des salutations (Chitchat direct sans SQL ni IA)
    chitchat = _get_chitchat_response(question)
    if chitchat:
        return jsonify({
            "success": True,
            "intention": "salutation",
            "criteres": {"intention": "salutation", "eleve": None, "classe": None},
            "donnees_trouvees": 0,
            "donnees": [],
            "reply": chitchat,
        })

    ecole_id = getattr(current_user, "ecole_id", None)
    annee_active = get_annee_active(ecole_id) if ecole_id else None
    annee_id = annee_active.id if annee_active else None

    # 2. Classification heuristique ultra-rapide (< 0.001s) en Python
    fast_intent = _fast_detect_intent_and_entities(question)
    if fast_intent:
        intent = fast_intent
        logger.info("Classification intention ultra-rapide (Python) : %s", intent)
    else:
        # Fallback IA locale uniquement si la question est complexe ou atypique
        intent = extract_query_intent(question)

    intention = intent.get("intention", "autre")
    classe_param = intent.get("classe")
    eleve_param = intent.get("eleve")
    periode_param = intent.get("periode")
    seuil_param = intent.get("seuil")

    # Assainissement strict des chaînes "null" / "none"
    if eleve_param and str(eleve_param).lower().strip() in ("null", "none", "inconnu", "undefined", "nil", "n/a", '""', "''", ""):
        eleve_param = None
    if classe_param and str(classe_param).lower().strip() in ("null", "none", "inconnu", "undefined", "nil", "n/a", '""', "''", ""):
        classe_param = None

    q_lower = question.lower()

    # 3. Détection par Regex du nom d'élève si l'IA locale l'a manqué
    if not eleve_param:
        patterns = [
            r"(?:qui\s+est\s+(?:l['’]élève\s+|l['’]eleve\s+|ce\s+|cet\s+|cette\s+)?|c['’]est\s+qui\s+|connais-tu\s+|tu\s+connais\s+|parle-moi\s+de\s+|parlez-moi\s+de\s+|qu['’]en\s+est-il\s+de\s+|infos?\s+sur\s+|renseigne-moi\s+sur\s+|dis-moi\s+sur\s+)([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
            r"(?:l['’]élève|l['’]eleve|eleve|élève)\s+([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
            r"(?:fiche|dossier|bulletin|notes?|absences?)\s+(?:de\s+|d['’]\s*)?([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
            r"(?:pour|sur)\s+([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)"
        ]
        for pat in patterns:
            m = re.search(pat, question, re.IGNORECASE)
            if m:
                cand = _clean_search_term(m.group(1))
                if cand and cand.lower() not in (
                    "la", "cette", "tous", "toutes", "chaque", "classe", "ecole", "école",
                    "son", "ses", "des", "une", "un", "mon", "notre", "leurs", "lui",
                    "null", "none", "inconnu", "undefined", "nil", "n/a", "qui", "ce"
                ):
                    eleve_param = cand
                    break

    # 3b. Fallback direct : si la saisie de l'utilisateur correspond au nom d'un élève de l'établissement
    if not eleve_param and ecole_id:
        clean_raw = _clean_search_term(question)
        if clean_raw and len(clean_raw) >= 2 and clean_raw.lower() not in (
            "bonjour", "bonsoir", "salut", "coucou", "merci", "aide", "test",
            "classe", "ecole", "notes", "absences", "fiche", "dossier", "oui", "non",
            "null", "none", "inconnu", "undefined", "qui", "quoi", "comment"
        ):
            cand_eleves = _search_eleves_in_ecole(clean_raw, ecole_id)
            if cand_eleves:
                target_cand = cand_eleves[0]
                eleve_param = f"{target_cand.prenom} {target_cand.nom}"
                if intention in ("autre", "fiche_eleve"):
                    intention = "fiche_eleve"

    # Assainissement après regex et fallback
    if eleve_param and str(eleve_param).lower().strip() in ("null", "none", "inconnu", "undefined", "nil", "n/a", '""', "''", ""):
        eleve_param = None

    # 4. Mémoire d'entité en session Flask (Résolution d'anaphores)
    has_anaphora = any(re.search(pat, q_lower) for pat in ANAPHORA_PATTERNS)
    last_eleve_id = session.get("ai_last_eleve_id")

    if not eleve_param and last_eleve_id and (has_anaphora or intention in ("fiche_eleve", "notes", "absences", "contact_parent")):
        cached_eleve = Eleve.query.filter_by(id=last_eleve_id, ecole_id=ecole_id).first()
        if cached_eleve:
            eleve_param = f"{cached_eleve.prenom} {cached_eleve.nom}"
            logger.info("Anaphore résolue via session : élève ID %s (%s)", cached_eleve.id, eleve_param)

            # Raffiner l'intention selon les mots-clés de la question de suivi
            if "absence" in q_lower:
                intention = "absences"
            elif "note" in q_lower or "moyenne" in q_lower:
                intention = "notes"
            elif any(w in q_lower for w in ("parent", "contact", "tel", "téléphone", "telephone", "email", "joindre", "appeler")):
                intention = "contact_parent"
            elif any(w in q_lower for w in ("fiche", "dossier", "info", "information", "tout", "complet")):
                intention = "fiche_eleve"

    # Vérification si l'utilisateur demande explicitement d'oublier ou de changer d'élève
    if any(w in q_lower for w in ("oublie", "nouvel élève", "changer d'élève", "autre élève", "recommencer")):
        session.pop("ai_last_eleve_id", None)
        session.pop("ai_last_eleve_nom", None)
        session.pop("ai_last_classe", None)
        session.modified = True
        eleve_param = None

    # 5. Garde-fous ciblés : si notes/absences/fiche sont demandées sans cible élève ni classe
    if intention in ("notes", "absences") and not eleve_param and not classe_param and not has_anaphora:
        # Si la question ne mentionne ni classe ni élève
        return jsonify({
            "success": True,
            "intention": intention,
            "criteres": intent,
            "donnees_trouvees": 0,
            "donnees": [],
            "reply": "Pour quel élève ou quelle classe souhaitez-vous consulter ces données ?",
        })

    if intention == "fiche_eleve" and not eleve_param:
        return jsonify({
            "success": True,
            "intention": intention,
            "criteres": intent,
            "donnees_trouvees": 0,
            "donnees": [],
            "reply": "Je suis à votre entière disposition ! De quel élève souhaitez-vous consulter le dossier ou les résultats ? Vous pouvez m'indiquer son nom ou son prénom.",
        })

    # 4. Raffinement automatique de l'intention
    if eleve_param:
        has_note = any(w in q_lower for w in ("note", "notes", "moyenne", "bulletin", "évaluation"))
        has_abs = any(w in q_lower for w in ("absence", "absences", "absent"))
        has_contact = any(w in q_lower for w in ("parent", "contact", "téléphone", "telephone", "numéro", "numero", "email", "joindre", "appeler"))
        has_fiche = any(w in q_lower for w in ("fiche", "dossier", "info", "information", "qui est", "c'est quoi", "profil", "tout savoir", "complet", "complete"))

        if (has_note and has_abs) or has_fiche or intention == "fiche_eleve":
            intention = "fiche_eleve"
        elif has_contact and not has_note and not has_abs:
            intention = "contact_parent"
        elif has_note and not has_abs:
            intention = "notes"
        elif has_abs and not has_note:
            intention = "absences"
        elif intention == "autre":
            intention = "fiche_eleve"

    records_summary: List[Dict[str, Any]] = []
    context_lines: List[str] = []

    # 5. Exécution des requêtes SQLAlchemy selon l'intention
    try:
        # FAST-PATH CHANTIER D : STATUT DE LA RENTRÉE SCOLAIRE
        if intention == "statut_rentree":
            annee_planifiee = (
                AnneeScolaire.query
                .filter_by(ecole_id=ecole_id, statut='planifiee')
                .order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc())
                .first()
            ) if ecole_id else None

            if not annee_planifiee:
                reply = (
                    "ℹ️ **Aucune rentrée planifiée en cours**\n\n"
                    "Aucune année scolaire au statut *planifiée* n'a été trouvée pour votre établissement. "
                    "Vous pouvez en créer une nouvelle depuis le menu **Années scolaires** pour démarrer le parcours de rentrée."
                )
                return jsonify({
                    "success": True,
                    "intention": "statut_rentree",
                    "criteres": intent,
                    "donnees_trouvees": 0,
                    "donnees": [],
                    "reply": reply,
                })

            from app.services.preparation_annee import get_etat_preparation_annee
            etat_prep = get_etat_preparation_annee(ecole_id, annee_planifiee.id)
            progression = etat_prep["progression"] if etat_prep else 0
            total_classes = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_planifiee.id).count()

            inscrits_cible = Inscription.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_planifiee.id).all()
            nb_confirmes = sum(1 for insc in inscrits_cible if insc.statut == 'inscrit')
            nb_preinscrits = sum(1 for insc in inscrits_cible if insc.statut == 'preinscrit')

            traites_passage = etat_prep["passage"]["traites"] if (etat_prep and etat_prep.get("passage")) else 0
            total_passage = etat_prep["passage"]["total_eleves"] if (etat_prep and etat_prep.get("passage")) else 0

            reply = (
                f"🎯 **Point de situation — Rentrée {annee_planifiee.nom}**\n\n"
                f"La préparation est actuellement complétée à **{progression}%** :\n"
                f"• **Structure pédagogique** : {total_classes} classe(s) créée(s)\n"
                f"• **Décisions du conseil** : {traites_passage}/{total_passage} élève(s) orienté(s)\n"
                f"• **Pointage financier** : {nb_confirmes} inscrit(s) confirmé(s) et {nb_preinscrits} préinscrit(s) en attente d'acompte\n\n"
                f"[Ouvrir l'Onboarding de rentrée](/annees/{annee_planifiee.id}/onboarding_rentree)"
            )

            return jsonify({
                "success": True,
                "intention": "statut_rentree",
                "criteres": intent,
                "donnees_trouvees": len(inscrits_cible),
                "donnees": [{
                    "annee_id": annee_planifiee.id,
                    "annee_nom": annee_planifiee.nom,
                    "progression": progression,
                    "total_classes": total_classes,
                    "nb_confirmes": nb_confirmes,
                    "nb_preinscrits": nb_preinscrits,
                }],
                "reply": reply,
            })

        # FAST-PATH CHANTIER D : RELANCE DES PRÉINSCRIPTIONS & ACOMPTES
        elif intention == "relance_reinscriptions":
            annee_planifiee = (
                AnneeScolaire.query
                .filter_by(ecole_id=ecole_id, statut='planifiee')
                .order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc())
                .first()
            ) if ecole_id else None

            if not annee_planifiee:
                reply = (
                    "ℹ️ **Aucune rentrée planifiée en cours**\n\n"
                    "Aucune année scolaire au statut *planifiée* n'est actuellement en préparation pour votre établissement."
                )
                return jsonify({
                    "success": True,
                    "intention": "relance_reinscriptions",
                    "criteres": intent,
                    "donnees_trouvees": 0,
                    "donnees": [],
                    "reply": reply,
                })

            preinscrits_query = (
                Inscription.query
                .filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_planifiee.id, statut='preinscrit')
                .join(Eleve, Eleve.id == Inscription.eleve_id)
                .outerjoin(Classe, Classe.id == Inscription.classe_id)
                .options(db.joinedload(Inscription.eleve), db.joinedload(Inscription.classe))
            )
            if classe_param:
                preinscrits_query = preinscrits_query.filter(Classe.nom.ilike(f"%{classe_param}%"))

            preinscrits = preinscrits_query.order_by(Classe.nom.asc(), Eleve.nom.asc()).all()

            if not preinscrits:
                reply = (
                    f"🎉 **Tous les élèves sont à jour !**\n\n"
                    f"Pour la rentrée **{annee_planifiee.nom}**, tous les dossiers d'élèves enregistrés ont validé leur inscription. "
                    f"Aucun élève n'est en attente d'acompte.\n\n"
                    f"[Accéder à l'Onboarding de rentrée](/annees/{annee_planifiee.id}/onboarding_rentree)"
                )
                return jsonify({
                    "success": True,
                    "intention": "relance_reinscriptions",
                    "criteres": intent,
                    "donnees_trouvees": 0,
                    "donnees": [],
                    "reply": reply,
                })

            lines = [
                f"📋 **Élèves préinscrits en attente d'acompte ({len(preinscrits)}) — Rentrée {annee_planifiee.nom}** :\n"
            ]
            records_data = []
            for insc in preinscrits[:15]:
                el = insc.eleve
                cl_nom = insc.classe.nom if insc.classe else "Sans classe"
                tel = el.contact_parent or el.telephone or "Non renseigné"
                records_data.append({
                    "eleve_id": el.id,
                    "nom_complet": f"{el.prenom} {el.nom}",
                    "classe": cl_nom,
                    "telephone": tel,
                })
                lines.append(f"• **{el.prenom} {el.nom}** ({cl_nom}) — Tél: `{tel}`")

            if len(preinscrits) > 15:
                lines.append(f"\n*... et {len(preinscrits) - 15} autre(s) élève(s) en attente.*")

            lines.append(f"\n[Gérer dans l'Onboarding de rentrée](/annees/{annee_planifiee.id}/onboarding_rentree)")
            reply = "\n".join(lines)

            return jsonify({
                "success": True,
                "intention": "relance_reinscriptions",
                "criteres": intent,
                "donnees_trouvees": len(preinscrits),
                "donnees": records_data,
                "reply": reply,
            })

        # A) FICHE COMPLÈTE DE L'ÉLÈVE
        elif intention == "fiche_eleve" and eleve_param:
            matched_eleves = _search_eleves_in_ecole(eleve_param, ecole_id) if ecole_id else []

            if not matched_eleves:
                context_lines.append(f"Aucun élève trouvé avec le nom '{eleve_param}' dans cet établissement.")
            else:
                # Mémorisation en session du premier élève trouvé
                target_el = matched_eleves[0]
                session["ai_last_eleve_id"] = target_el.id
                session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                session.modified = True

                for el in matched_eleves:
                    dossier = _build_eleve_dossier(el, ecole_id, annee_id)
                    records_summary.append(dossier)

                    context_lines.append(f"=== DOSSIER COMPLET DE L'ÉLÈVE : {dossier['nom_complet']} ===")
                    context_lines.append(
                        f"- Identité : Matricule {dossier['matricule']} | Classe : {dossier['classe']} | "
                        f"Statut : {dossier['statut']} | Né(e) le : {dossier['date_naissance']} à {dossier['lieu_naissance']} | "
                        f"Adresse : {dossier['adresse']}"
                    )
                    context_lines.append(
                        f"- Contact Parent/Tuteur : {dossier['parent_nom'] or 'Non renseigné'} | "
                        f"Tél: {dossier['parent_tel']} | Email: {dossier['parent_email']}"
                    )
                    context_lines.append(
                        f"- Bilan des Absences : {dossier['total_absences']} absence(s) au total "
                        f"({dossier['absences_justifiees']} justifiée(s), {dossier['absences_non_justifiees']} non justifiée(s))"
                    )
                    if dossier["absences_recentes"]:
                        context_lines.append(f"  * Dernières absences : {'; '.join(dossier['absences_recentes'])}")
                    else:
                        context_lines.append("  * Aucune absence enregistrée.")

                    if dossier["moyenne_generale"] is not None:
                        context_lines.append(
                            f"- Performances Scolaires : Moyenne Générale de {dossier['moyenne_generale']}/20 "
                            f"({dossier['nombre_notes']} note(s) enregistrée(s))"
                        )
                    else:
                        context_lines.append(f"- Performances Scolaires : {dossier['nombre_notes']} note(s) enregistrée(s)")

                    if dossier["notes_details"]:
                        context_lines.append(f"  * Relevé des notes : {'; '.join(dossier['notes_details'])}")
                    else:
                        context_lines.append("  * Aucune note enregistrée.")

                    context_lines.append(
                        f"- Situation Financière : Frais annuels : {dossier['frais_annuels']} FCFA | "
                        f"Total payé : {dossier['total_paye']} FCFA | Reste dû : {dossier['reste_a_payer']} FCFA"
                    )

        # B) ABSENCES
        elif intention == "absences":
            if eleve_param:
                matched_eleves = _search_eleves_in_ecole(eleve_param, ecole_id) if ecole_id else []
                if not matched_eleves:
                    context_lines.append(f"Aucun élève trouvé avec le nom '{eleve_param}' pour consulter les absences.")
                else:
                    target_el = matched_eleves[0]
                    session["ai_last_eleve_id"] = target_el.id
                    session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                    session.modified = True

                    for el in matched_eleves:
                        dossier = _build_eleve_dossier(el, ecole_id, annee_id)
                        records_summary.append(dossier)
                        context_lines.append(f"=== ABSENCES DE L'ÉLÈVE : {dossier['nom_complet']} (Classe : {dossier['classe']}) ===")
                        context_lines.append(
                            f"- Total absences : {dossier['total_absences']} "
                            f"({dossier['absences_justifiees']} justifiée(s), {dossier['absences_non_justifiees']} non justifiée(s))"
                        )
                        if dossier["absences_recentes"]:
                            context_lines.append(f"- Dernières absences : {'; '.join(dossier['absences_recentes'])}")
                        else:
                            context_lines.append("- Aucune absence enregistrée pour cet élève.")
                        context_lines.append(f"- Contact du parent : Tél: {dossier['parent_tel']} | Email: {dossier['parent_email']}")
            else:
                # Absences globales ou par classe
                query = (
                    db.session.query(
                        Eleve.id.label("eleve_id"),
                        Eleve.nom,
                        Eleve.prenom,
                        Classe.nom.label("classe_nom"),
                        func.count(Absence.id).label("total_absences"),
                        func.sum(db.case((Absence.justifiee == True, 1), else_=0)).label("justifiees"),
                        func.sum(db.case((Absence.justifiee == False, 1), else_=0)).label("non_justifiees"),
                    )
                    .join(Inscription, Inscription.eleve_id == Eleve.id)
                    .join(Classe, Classe.id == Inscription.classe_id)
                    .outerjoin(Absence, Absence.inscription_id == Inscription.id)
                    .filter(Inscription.ecole_id == ecole_id)
                )

                if annee_id:
                    query = query.filter(Inscription.annee_scolaire_id == annee_id)
                if classe_param:
                    query = query.filter(Classe.nom.ilike(f"%{classe_param}%"))

                query = query.group_by(Eleve.id, Eleve.nom, Eleve.prenom, Classe.nom)

                if seuil_param is not None and seuil_param > 0:
                    query = query.having(func.count(Absence.id) >= seuil_param)
                else:
                    query = query.having(func.count(Absence.id) > 0)

                results = query.order_by(func.count(Absence.id).desc()).limit(30).all()

                for r in results:
                    rec = {
                        "eleve": f"{r.prenom} {r.nom}",
                        "classe": r.classe_nom,
                        "total_absences": int(r.total_absences or 0),
                        "justifiees": int(r.justifiees or 0),
                        "non_justifiees": int(r.non_justifiees or 0),
                    }
                    records_summary.append(rec)
                    context_lines.append(
                        f"- {rec['eleve']} ({rec['classe']}) : {rec['total_absences']} absence(s) "
                        f"dont {rec['non_justifiees']} non justifiée(s) et {rec['justifiees']} justifiée(s)"
                    )

        # C) NOTES ET MOYENNES
        elif intention == "notes":
            if eleve_param:
                matched_eleves = _search_eleves_in_ecole(eleve_param, ecole_id) if ecole_id else []
                if not matched_eleves:
                    context_lines.append(f"Aucun élève trouvé avec le nom '{eleve_param}' pour consulter les notes.")
                else:
                    target_el = matched_eleves[0]
                    session["ai_last_eleve_id"] = target_el.id
                    session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                    session.modified = True

                    for el in matched_eleves:
                        dossier = _build_eleve_dossier(el, ecole_id, annee_id)
                        records_summary.append(dossier)
                        context_lines.append(f"=== NOTES & RÉSULTATS DE L'ÉLÈVE : {dossier['nom_complet']} (Classe : {dossier['classe']}) ===")
                        if dossier["moyenne_generale"] is not None:
                            context_lines.append(f"- Moyenne générale actuelle : {dossier['moyenne_generale']}/20")
                        else:
                            context_lines.append("- Moyenne générale : Non calculée")

                        if dossier["notes_details"]:
                            context_lines.append(f"- Relevé des notes ({dossier['nombre_notes']} note(s)) : {'; '.join(dossier['notes_details'])}")
                        else:
                            context_lines.append("- Aucune note enregistrée pour cet élève.")

                        context_lines.append(
                            f"- Absences enregistrées en parallèle : {dossier['total_absences']} "
                            f"({dossier['absences_non_justifiees']} non justifiée(s))"
                        )
            else:
                query = (
                    db.session.query(
                        Eleve.nom,
                        Eleve.prenom,
                        Classe.nom.label("classe_nom"),
                        Cours.nom.label("cours_nom"),
                        Note.valeur,
                        Note.coefficient,
                        Note.periode,
                    )
                    .join(Inscription, Inscription.eleve_id == Eleve.id)
                    .join(Classe, Classe.id == Inscription.classe_id)
                    .join(Note, Note.inscription_id == Inscription.id)
                    .join(Cours, Cours.id == Note.cours_id)
                    .filter(Inscription.ecole_id == ecole_id)
                )

                if annee_id:
                    query = query.filter(Inscription.annee_scolaire_id == annee_id)
                if classe_param:
                    query = query.filter(Classe.nom.ilike(f"%{classe_param}%"))
                if periode_param:
                    query = query.filter(Note.periode.ilike(f"%{periode_param}%"))
                if seuil_param is not None:
                    query = query.filter(Note.valeur >= float(seuil_param))

                results = query.order_by(Classe.nom.asc(), Eleve.nom.asc(), Note.valeur.desc()).limit(30).all()

                for r in results:
                    rec = {
                        "eleve": f"{r.prenom} {r.nom}",
                        "classe": r.classe_nom,
                        "matiere": r.cours_nom,
                        "note": float(r.valeur) if r.valeur is not None else None,
                        "coefficient": float(r.coefficient or 1.0),
                        "periode": r.periode,
                    }
                    records_summary.append(rec)
                    context_lines.append(
                        f"- {rec['eleve']} ({rec['classe']}) en {rec['matiere']} : {rec['note']}/20 "
                        f"(coef {rec['coefficient']}) [{rec['periode'] or 'Période standard'}]"
                    )

        # D) CONTACTS PARENTS / TUTEURS
        elif intention == "contact_parent":
            if eleve_param:
                matched_eleves = _search_eleves_in_ecole(eleve_param, ecole_id) if ecole_id else []
                if not matched_eleves:
                    context_lines.append(f"Aucun élève trouvé avec le nom '{eleve_param}'.")
                else:
                    target_el = matched_eleves[0]
                    session["ai_last_eleve_id"] = target_el.id
                    session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                    session.modified = True

                    for el in matched_eleves:
                        dossier = _build_eleve_dossier(el, ecole_id, annee_id)
                        records_summary.append(dossier)
                        context_lines.append(f"=== CONTACT PARENT DE L'ÉLÈVE : {dossier['nom_complet']} ({dossier['classe']}) ===")
                        context_lines.append(f"- Parent/Tuteur : {dossier['parent_nom'] or 'Non renseigné'}")
                        context_lines.append(f"- Téléphone : {dossier['parent_tel']}")
                        context_lines.append(f"- Email : {dossier['parent_email']}")
                        context_lines.append(f"- Adresse : {dossier['adresse']}")
            else:
                query = (
                    db.session.query(
                        Eleve.nom,
                        Eleve.prenom,
                        Classe.nom.label("classe_nom"),
                        Eleve.contact_parent,
                        Eleve.email_parent,
                        Eleve.telephone,
                    )
                    .join(Inscription, Inscription.eleve_id == Eleve.id)
                    .join(Classe, Classe.id == Inscription.classe_id)
                    .filter(Inscription.ecole_id == ecole_id)
                )

                if annee_id:
                    query = query.filter(Inscription.annee_scolaire_id == annee_id)
                if classe_param:
                    query = query.filter(Classe.nom.ilike(f"%{classe_param}%"))

                results = query.order_by(Classe.nom.asc(), Eleve.nom.asc()).limit(25).all()

                for r in results:
                    tel = r.contact_parent or r.telephone or "Non renseigné"
                    email = r.email_parent or "Non renseigné"
                    rec = {
                        "eleve": f"{r.prenom} {r.nom}",
                        "classe": r.classe_nom,
                        "telephone_parent": tel,
                        "email_parent": email,
                    }
                    records_summary.append(rec)
                    context_lines.append(f"- {rec['eleve']} ({rec['classe']}) : Tél: {tel} | Email: {email}")

        # E) INTENTION GÉNÉRALE / CONSEILS ADMINISTRATIFS
        else:
            reply = chat_with_assistant(question, history=history)
            return jsonify({
                "success": True,
                "intention": "autre",
                "criteres": intent,
                "donnees_trouvees": 0,
                "donnees": [],
                "reply": reply,
            })

    except Exception as db_exc:
        logger.error("Erreur lors de l'exécution de la requête SQLAlchemy dans query-data : %s", db_exc)
        return jsonify({
            "success": False,
            "error": f"Erreur lors de la consultation des données scolaires : {db_exc}"
        }), 500

    # 6. Synthèse certifiée ou rendu direct structuré ultra-rapide
    is_analytical = any(w in q_lower for w in ("analyse", "avis", "conseil", "conseils", "pourquoi", "explique", "rédige", "redige", "lettre", "convocation", "rapport", "comparaison"))

    if intention == "fiche_eleve" and records_summary and not is_analytical:
        final_reply = _format_fiche_eleve_markdown(records_summary[0])
        if len(records_summary) > 1:
            noms_autres = ", ".join(f"{r['nom_complet']} ({r['classe']})" for r in records_summary[1:3])
            final_reply += f"\n\n*Note : D'autres élèves correspondent également : {noms_autres}.*"
    elif intention == "contact_parent" and records_summary and not is_analytical:
        if eleve_param or len(records_summary) == 1:
            final_reply = _format_contact_parent_markdown(records_summary[0])
            if len(records_summary) > 1:
                noms_autres = ", ".join(f"{r.get('nom_complet', r.get('eleve'))}" for r in records_summary[1:3])
                final_reply += f"\n\n*Note : Autres correspondances : {noms_autres}.*"
        else:
            lines = [f"📞 **Contacts des Parents — {classe_param or 'Établissement'}**\n"]
            for r in records_summary[:15]:
                el_nom = r.get("eleve") or r.get("nom_complet")
                cl_nom = r.get("classe", "")
                tel = r.get("telephone_parent") or r.get("parent_tel", "Non renseigné")
                mail = r.get("email_parent") or r.get("parent_email", "Non renseigné")
                lines.append(f"• **{el_nom}** ({cl_nom}) : Tél: `{tel}` | Email: {mail}")
            final_reply = "\n".join(lines)
    else:
        # Synthèse IA avec injection du contexte certifié
        data_context = "\n".join(context_lines) if context_lines else "Aucun enregistrement ne correspond aux critères demandés dans l'établissement."
        summary_prompt = (
            f"Question du directeur : \"{question}\"\n\n"
            f"Données scolaires certifiées de l'établissement KLASORA :\n"
            f"{data_context}\n\n"
            "Consignes impératives :\n"
            "1. Rédige une réponse humaine, intelligente, fluide et chaleureuse au directeur en vous basant STRICTEMENT sur ces données réelles.\n"
            "2. Si un élève ou une information n'a pas été trouvé, indique-le poliment et avec empathie sans rien inventer.\n"
            "3. Mets en valeur les points clés (notes, moyenne, absences, contacts) de manière claire, concise et naturelle.\n"
            "4. INTERDICTION STRICTE DE SIGNATURE : Tu réponds dans une messagerie instantanée directe. Ne termine JAMAIS par 'Cordialement', 'Bien cordialement', '[Nom du Directeur]', '[Signature]', ni aucun texte entre crochets comme [Nom].\n"
            "5. RÈGLE IMPÉRATIVE DE VOCABULAIRE : Ne mentionne JAMAIS de termes techniques tels que 'base de données', 'requête', 'SQL', 'serveur', 'null' ou 'système'. Exprime-toi toujours de façon humaine, élégante et axée sur la scolarité."
        )

        final_reply = clean_assistant_reply(
            query_assistant(summary_prompt, system_context=SYSTEM_ASSISTANT, temperature=0.2, history=history)
        )

    # Ajout automatique du bouton interactif d'accès au dossier si un élève unique est ciblé
    target_el_id = records_summary[0].get("eleve_id") if (records_summary and len(records_summary) == 1 and records_summary[0].get("eleve_id")) else session.get("ai_last_eleve_id")
    if target_el_id and f"/eleve/{target_el_id}" not in final_reply and intention in ("fiche_eleve", "absences", "notes", "contact_parent"):
        final_reply += f"\n\n[Voir son dossier complet](/eleve/{target_el_id})"

    return jsonify({
        "success": True,
        "intention": intention,
        "criteres": intent,
        "donnees_trouvees": len(records_summary),
        "donnees": records_summary,
        "reply": final_reply,
    })
