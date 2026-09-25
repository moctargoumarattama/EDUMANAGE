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
import json
import logging
import re
from typing import Any, Dict, Generator, List, Optional
import unicodedata

from flask import Blueprint, Response, jsonify, request, session, stream_with_context
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
    stream_assistant,
)
from app.authorization import role_required
from app.models import Absence, AnneeScolaire, Classe, Cours, Eleve, Inscription, Note, Paiement, Utilisateur
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


# Mots-clés pour l'analyse
GREETING_TOKENS = {
    "bonjour", "bonjours", "bonsoir", "bonsoirs", "salut", "saluts", "coucou", "hello",
    "hi", "hey", "bjr", "slt", "salam", "yo", "morning", "allo"
}

POLITENESS_KEYWORDS = {
    "merci", "remercie", "thank", "thanks", "au revoir", "a bientot", "à bientôt", "adieu", "bye"
}

SCHOOL_KEYWORDS = {
    "classe", "classes", "eleve", "eleves", "élève", "élèves",
    "note", "notes", "absence", "absences", "moyenne", "moyennes",
    "bulletin", "bulletins", "parent", "parents", "cours", "matiere", "matières", "matieres",
    "dossier", "fiche", "trimestre", "semestre", "scolarite", "scolarité",
    "frais", "paiement", "paiements", "solde", "effectif", "effectifs",
    "prof", "profs", "professeur", "professeurs", "enseignant", "enseignants",
    "impaye", "impayes", "rentree", "reinscription"
}


def _get_chitchat_response(text: str) -> Optional[str]:
    """
    Interception prioritaire des salutations, questions conversationnelles et formules de politesse :
    Extrêmement tolérant aux pluriels ("bonjours"), fautes de frappe, abréviations ("bjr", "slt"),
    ponctuations et tournures conversationnelles ("comment tu vas", "qui es-tu", etc.).
    Répond instantanément (< 1ms) sans solliciter le réseau neuronal.
    """
    if not text:
        return None
    raw = text.lower().strip()
    norm = _normalize_text(raw)
    tokens = set(norm.split())

    # S'il y a un mot-clé scolaire ou une question de données (ex: "effectif de l'école", "notes de"),
    # laisser le moteur de données ou fast-path traiter la demande
    if (tokens & SCHOOL_KEYWORDS) and len(tokens) >= 2:
        return None

    # 1. Expressions de remerciement
    if any(m in norm for m in ("merci", "remercie", "thank", "thanks")):
        return (
            "Je vous en prie ! C'est un réel plaisir de vous assister. "
            "N'hésitez pas si vous avez d'autres vérifications à effectuer sur vos élèves ou vos classes."
        )

    # 2. Au revoir / Départ
    if any(m in norm for m in ("au revoir", "a bientot", "bonne journee", "bonne soiree", "a plus", "adieu", "bye")):
        return "Au revoir Monsieur/Madame la Direction ! Excellente journée et à très bientôt sur KLASORA."

    # 3. "Comment ça va ?" / "Comment vas-tu ?" / "Tu vas bien ?"
    if any(p in norm for p in ("comment ca va", "comment tu vas", "comment allez vous", "ca va", "tu vas bien", "vous allez bien")):
        return (
            "Je me porte à merveille, merci ! 😊 Toujours opérationnel pour vous assister dans le pilotage de votre établissement.\n\n"
            "Que souhaitez-vous vérifier aujourd'hui ?\n"
            "• 📊 *« Effectif de l'école »* ou *« Effectif 6ème A »*\n"
            "• ⏱️ *« Qui a le plus d'absences ? »*\n"
            "• 🏆 *« Meilleurs élèves »*\n"
            "• 👤 *« Fiche d'un élève »*"
        )

    # 4. "Qui es-tu ?" / "Que sais-tu faire ?" / "Aide" / "Rôle"
    if any(p in norm for p in (
        "qui es tu", "qui est tu", "t es qui", "tes qui", "c est quoi ton role", "tu sers a quoi",
        "que peux tu faire", "que sais tu faire", "qu est ce que tu peux faire", "aide moi", "a l aide", "aide", "comment tu fonctionnes"
    )):
        return (
            "Je suis l'**Assistant IA de Direction KLASORA** 🎓\n\n"
            "Je réponds instantanément à vos demandes de gestion et de pilotage scolaire :\n"
            "• 📊 **Statistiques & Effectifs :** *« Effectif de l'école »*, *« Effectif de la 6ème A »*\n"
            "• 👤 **Dossiers & Fiches élèves :** *« Fiche de Mamadou »*, *« Notes de Fatou »*\n"
            "• ⏱️ **Assiduité & Discipline :** *« Qui a le plus d'absences ? »*, *« Absences critiques »*\n"
            "• 🏆 **Résultats & Palmarès :** *« Meilleurs élèves »*, *« Bilan des notes »*\n"
            "• 👨‍🏫 **Équipe Pédagogique :** *« Liste des professeurs »*\n"
            "• 💳 **Finances :** *« Qui a des impayés ? »*, *« Reste à payer »*\n"
            "• ✍️ **Rédaction officielle :** Modèles de convocation parentale et courriers aux familles."
        )

    # 5. Salutations directes (avec tolérance sur fautes de frappe "bonjours", pluriels, abréviations)
    has_greeting_token = bool(tokens & GREETING_TOKENS)
    starts_with_greeting = any(norm.startswith(g) for g in GREETING_TOKENS)

    # Si c'est un message court débutant ou contenant une salutation
    if (has_greeting_token or starts_with_greeting) and len(tokens) <= 6:
        return (
            "Bonjour Monsieur/Madame la Direction ! 👋\n\n"
            "Je suis votre assistant KLASORA. Comment puis-je vous aider aujourd'hui ?\n\n"
            "Voici quelques exemples de questions courantes :\n"
            "• 📊 *« Effectif total de l'école »* ou *« Effectif de la 6ème A »*\n"
            "• ⏱️ *« Qui a le plus d'absences ? »*\n"
            "• 🏆 *« Quels sont les meilleurs élèves ? »*\n"
            "• 👨‍🏫 *« Liste des professeurs »*\n"
            "• 👤 *« Fiche complète de [Nom de l'élève] »*"
        )

    return None


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


def _build_eleve_dossier(eleve: Eleve, ecole_id: int, annee_id: Optional[int] = None, periode: Optional[str] = None) -> Dict[str, Any]:
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

    if periode:
        notes = [n for n in notes if n.periode and periode.lower() in n.periode.lower()]

    notes_sorted = sorted(notes, key=lambda n: n.date_evaluation or datetime.min, reverse=True)
    notes_list_str = []
    for n in notes_sorted[:15]:
        c_nom = n.cours.nom if getattr(n, "cours", None) else "Matière"
        coef = f" (coef {n.coefficient})" if n.coefficient and n.coefficient != 1.0 else ""
        per = f" [{n.periode}]" if n.periode else ""
        notes_list_str.append(f"{c_nom} : {n.valeur}/20{coef}{per}")

    if notes:
        total_pondere = sum(n.valeur * (n.coefficient or 1.0) for n in notes)
        total_coeff = sum((n.coefficient or 1.0) for n in notes)
        moyenne = round(total_pondere / total_coeff, 2) if total_coeff > 0 else 0.0
    else:
        moyenne = eleve.moyenne_generale() if not annee_id and not periode else None

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


def _format_notes_eleve_markdown(d: Dict[str, Any], periode: Optional[str] = None) -> str:
    """Formatte le bilan des notes d'un élève avec la nuance chirurgicale 'Pas encore saisi' vs 'Moyenne certifiée'."""
    el_id = d.get("eleve_id")
    nom = d.get("nom_complet", "Élève")
    classe = d.get("classe", "Non assignée")
    nb_notes = d.get("nombre_notes", 0)
    moyenne = d.get("moyenne_generale")
    notes_details = d.get("notes_details") or []
    per_str = f" pour le **{periode}**" if periode else ""

    if nb_notes == 0:
        return (
            f"ℹ️ **{nom}** est bien inscrit(e) en classe de **{classe}**, mais **aucun professeur n'a encore enregistré d'évaluation notée**{per_str} pour lui/elle dans le système.\n\n"
            f"[Consulter son dossier élève](/eleve/{el_id})"
        )

    lines = [
        f"📊 **Résultats & Notes — {nom} ({classe})**{per_str}\n",
        f"• 🎯 **Moyenne générale :** **{moyenne}/20** ({nb_notes} évaluation(s) enregistrée(s))\n",
        "**Détail des évaluations :**"
    ]
    for nd in notes_details[:10]:
        lines.append(f"• {nd}")

    if len(notes_details) > 10:
        lines.append(f"\n*... et {len(notes_details) - 10} autre(s) note(s).*")

    if el_id:
        lines.extend([
            "",
            f"[Consulter son bulletin complet](/eleve/{el_id})"
        ])
    return "\n".join(lines).strip()


def _format_absences_eleve_markdown(d: Dict[str, Any]) -> str:
    """Formatte l'assiduité d'un élève avec nuance chirurgicale (exemplaire vs historique)."""
    el_id = d.get("eleve_id")
    nom = d.get("nom_complet", "Élève")
    classe = d.get("classe", "Non assignée")
    total_abs = d.get("total_absences", 0)
    just = d.get("absences_justifiees", 0)
    non_just = d.get("absences_non_justifiees", 0)
    recentes = d.get("absences_recentes") or []
    parent_tel = d.get("parent_tel") or "Non renseigné"

    if total_abs == 0:
        return (
            f"👏 **Assiduité exemplaire !**\n\n"
            f"**{nom}** ({classe}) n'a **aucune absence enregistrée** depuis le début de l'année scolaire.\n\n"
            f"[Consulter son dossier élève](/eleve/{el_id})"
        )

    lines = [
        f"⏱️ **Bilan des Absences — {nom} ({classe})**\n",
        f"• **Total :** **{total_abs} absence(s)** ({just} justifiée(s), **{non_just} non justifiée(s)**)",
        f"• 📞 **Contact responsable :** `{parent_tel}`\n",
    ]
    if recentes:
        lines.append("**Derniers signalements d'absence :**")
        for r in recentes:
            lines.append(f"• {r}")

    if el_id:
        lines.extend([
            "",
            f"[Gérer les absences de l'élève](/eleve/{el_id})"
        ])
    return "\n".join(lines).strip()


MATIERES_PATTERNS = {
    "Mathématiques": [
        r"\bmaths?\b", r"\bmath[eé]matiques?\b", r"\balg[eè]bre\b", r"\bg[eé]om[eé]trie\b"
    ],
    "Français": [
        r"\bfran[cç]ais\b", r"\bdict[eé]e\b", r"\br[eé]daction\b", r"\blitt[eé]rature\b", r"\bgrammaire\b", r"\bconjugaison\b"
    ],
    "SVT": [
        r"\bsvt\b", r"\bsciences?\s+de\s+la\s+vie\b", r"\bbiologie\b", r"\bsciences?\s+naturelles?\b", r"\bscience\b", r"\bsciences\b"
    ],
    "Physique-Chimie": [
        r"\bphysique\b", r"\bchimie\b", r"\bpc\b", r"\bphysique[- ]chimie\b"
    ],
    "Histoire-Géographie": [
        r"\bhistoire\b", r"\bg[eé]ographie\b", r"\bg[eé]o\b", r"\bhg\b", r"\bhistoire[- ]g[eé]o\b"
    ],
    "Anglais": [
        r"\banglais\b", r"\benglish\b"
    ],
    "Philosophie": [
        r"\bphilo\b", r"\bphilosophie\b"
    ],
    "EPS": [
        r"\beps\b", r"\bsport\b", r"\b[eé]ducation\s+physique\b"
    ],
    "Informatique": [
        r"\binformatique\b", r"\binfo\b", r"\btic\b", r"\btechnologie\b"
    ],
    "Arabe": [
        r"\barabe\b"
    ],
    "Espagnol": [
        r"\bespagnol\b"
    ],
    "Allemand": [
        r"\ballemand\b"
    ],
}


def _extract_matiere(raw: str, q_norm: str) -> Optional[str]:
    """Extrait avec haute tolérance aux variantes et fautes la matière scolaire demandée."""
    for matiere, patterns in MATIERES_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, raw, re.IGNORECASE) or re.search(pat, q_norm, re.IGNORECASE):
                return matiere
    return None


def _extract_periode(raw: str, q_norm: str) -> Optional[str]:
    """Extrait la période trimestrielle ou semestrielle avec haute tolérance (ex: 1er trimestre, T1, S2)."""
    # Trimestre 1
    if re.search(r"\b(?:1er|premier|1ere|1e|t1|1)\s*trimestre\b|\btrimestre\s*(?:1|un|ier)\b|\bt1\b", raw, re.IGNORECASE) or re.search(r"\b(?:1er|premier|1ere|1e|t1|1)\s*trimestre\b|\btrimestre\s*(?:1|un)\b|\bt1\b", q_norm, re.IGNORECASE):
        return "Trimestre 1"
    # Trimestre 2
    if re.search(r"\b(?:2[eè]me|deuxi[eè]me|2e|t2|2)\s*trimestre\b|\btrimestre\s*(?:2|deux|ieme)\b|\bt2\b", raw, re.IGNORECASE) or re.search(r"\b(?:2eme|deuxieme|2e|t2|2)\s*trimestre\b|\btrimestre\s*(?:2|deux)\b|\bt2\b", q_norm, re.IGNORECASE):
        return "Trimestre 2"
    # Trimestre 3
    if re.search(r"\b(?:3[eè]me|troisi[eè]me|3e|t3|3)\s*trimestre\b|\btrimestre\s*(?:3|trois|ieme)\b|\bt3\b", raw, re.IGNORECASE) or re.search(r"\b(?:3eme|troisieme|3e|t3|3)\s*trimestre\b|\btrimestre\s*(?:3|trois)\b|\bt3\b", q_norm, re.IGNORECASE):
        return "Trimestre 3"
    # Semestre 1
    if re.search(r"\b(?:1er|premier|1ere|1e|s1|1)\s*semestre\b|\bsemestre\s*(?:1|un|ier)\b|\bs1\b", raw, re.IGNORECASE) or re.search(r"\b(?:1er|premier|1ere|1e|s1|1)\s*semestre\b|\bsemestre\s*(?:1|un)\b|\bs1\b", q_norm, re.IGNORECASE):
        return "Semestre 1"
    # Semestre 2
    if re.search(r"\b(?:2[eè]me|deuxi[eè]me|2e|s2|2)\s*semestre\b|\bsemestre\s*(?:2|deux|ieme)\b|\bs2\b", raw, re.IGNORECASE) or re.search(r"\b(?:2eme|deuxieme|2e|s2|2)\s*semestre\b|\bsemestre\s*(?:2|deux)\b|\bs2\b", q_norm, re.IGNORECASE):
        return "Semestre 2"
    return None


def _extract_annee_cible(raw: str, q_norm: str, ecole_id: Optional[int]) -> Optional[int]:
    """Identifie si la requête vise une année scolaire spécifique ou antérieure."""
    if not ecole_id:
        return None

    # Année passée / précédente
    if re.search(r"\b(?:l['’]an(?:n[eé]e)?\s+derni[eè]re?|l['’]an\s+pass[eé]|an(?:n[eé]e)?\s+pr[eé]c[eé]dente?)\b", raw, re.IGNORECASE) or any(k in q_norm for k in ("annee derniere", "an dernier", "annee passee", "annee precedente")):
        annee_active = get_annee_active(ecole_id)
        prev = (
            AnneeScolaire.query.filter_by(ecole_id=ecole_id)
            .filter(AnneeScolaire.id != (annee_active.id if annee_active else 0))
            .order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc())
            .first()
        )
        if prev:
            return prev.id

    # Année explicite (ex: 2024-2025)
    m = re.search(r"\b(20\d{2})[-/](20\d{2})\b", raw)
    if m:
        y_name = f"{m.group(1)}-{m.group(2)}"
        annee_cand = AnneeScolaire.query.filter_by(ecole_id=ecole_id).filter(AnneeScolaire.nom.ilike(f"%{y_name}%")).first()
        if annee_cand:
            return annee_cand.id

    return None


def _format_homonymes_choice(candidates: List[Eleve], search_term: str, ecole_id: int) -> str:
    """Formatte une proposition claire et interactive pour lever l'ambiguïté des homonymes."""
    lines = [
        f"👥 **Plusieurs élèves correspondent à votre recherche « {search_term} » ({len(candidates)} élèves trouvés) :**\n",
        "Afin d'afficher les informations exactes sans ambiguïté, veuillez sélectionner l'élève souhaité :\n"
    ]
    for idx, el in enumerate(candidates, 1):
        inscr = [i for i in el.inscriptions if i.ecole_id == ecole_id]
        cl_nom = inscr[-1].classe.nom if (inscr and inscr[-1].classe) else "Non assignée"
        mat = el.code_parent or f"ELV-{el.id}"
        nom_complet = f"{el.prenom} {el.nom}"
        lines.append(
            f"**{idx}. {nom_complet}** — Classe de **{cl_nom}** (Matricule : `{mat}`)\n"
            f"👉 [Consulter {el.prenom} ({cl_nom})](prompt:Fiche de {nom_complet} {cl_nom}) • [Fiche détaillée](/eleve/{el.id})\n"
        )
    return "\n".join(lines).strip()


def _handle_notes_matiere(eleve: Eleve, matiere_nom: str, ecole_id: int, annee_id: Optional[int], periode: Optional[str]) -> str:
    """Génère un retour ultra-précis des notes dans une matière spécifique avec nom de l'enseignant et moyenne."""
    inscr = [i for i in eleve.inscriptions if i.ecole_id == ecole_id]
    inscr_active = next((i for i in inscr if i.annee_scolaire_id == annee_id), None) if annee_id else (inscr[-1] if inscr else None)
    classe = inscr_active.classe if inscr_active else None
    classe_nom = classe.nom if classe else "Classe non assignée"

    # Vérification des cours de cette matière dispensés dans la classe
    cours_classe = []
    if classe:
        cours_classe = Cours.query.filter_by(classe_id=classe.id, ecole_id=ecole_id).filter(Cours.nom.ilike(f"%{matiere_nom}%")).all()
        if not cours_classe:
            m_norm = _normalize_text(matiere_nom)
            for c in Cours.query.filter_by(classe_id=classe.id, ecole_id=ecole_id).all():
                if m_norm in _normalize_text(c.nom) or _normalize_text(c.nom) in m_norm:
                    cours_classe.append(c)

    # Récupération des notes de l'élève
    notes_q = Note.query.filter_by(eleve_id=eleve.id, ecole_id=ecole_id)
    if annee_id:
        notes_q = notes_q.filter_by(annee_id=annee_id)
    if periode:
        notes_q = notes_q.filter(Note.periode.ilike(f"%{periode}%"))

    all_notes = notes_q.all()
    matching_notes = []
    for n in all_notes:
        c_nom = n.cours.nom if n.cours else ""
        if _normalize_text(matiere_nom) in _normalize_text(c_nom) or _normalize_text(c_nom) in _normalize_text(matiere_nom):
            matching_notes.append(n)

    nom_eleve = f"{eleve.prenom} {eleve.nom}"
    prof_nom = None
    all_cand_cours = list(cours_classe) + [n.cours for n in matching_notes if n.cours]
    for c in all_cand_cours:
        if c:
            if c.professeur:
                prof_nom = f"{c.professeur.prenom} {c.professeur.nom}".strip()
                break
            elif c.professeur_id:
                u = Utilisateur.query.get(c.professeur_id)
                if u:
                    prof_nom = f"{u.prenom} {u.nom}".strip()
                    break

    prof_str = f" (Enseignant : **{prof_nom}**)" if prof_nom else ""
    per_str = f" pour le **{periode}**" if periode else ""

    if not matching_notes:
        if not cours_classe and classe:
            return (
                f"ℹ️ La matière **{matiere_nom}** n'est pas enseignée en classe de **{classe_nom}**.\n\n"
                f"[Consulter l'emploi du temps](/emploi/classe/{classe.id}/imprimer)"
            )
        else:
            return (
                f"ℹ️ **{matiere_nom} — {nom_eleve} ({classe_nom})**{prof_str}\n\n"
                f"L'élève suit bien cette discipline, mais **aucun professeur n'a encore enregistré d'évaluation notée**{per_str} pour lui dans le système.\n\n"
                f"[Consulter son dossier élève](/eleve/{eleve.id})"
            )

    total_ponderation = sum(n.valeur * (n.coefficient or 1.0) for n in matching_notes)
    total_coefs = sum(n.coefficient or 1.0 for n in matching_notes)
    moyenne_matiere = round(total_ponderation / total_coefs, 2) if total_coefs > 0 else 0.0

    lines = [
        f"📚 **Résultats en {matiere_nom} — {nom_eleve} ({classe_nom})**{prof_str}\n",
        f"• **Moyenne dans la discipline :** **{moyenne_matiere}/20** ({len(matching_notes)} évaluation(s))\n",
        "**Détail des évaluations enregistrées :**"
    ]
    for n in sorted(matching_notes, key=lambda x: x.date_evaluation or datetime.min, reverse=True):
        d_str = n.date_evaluation.strftime("%d/%m/%Y") if n.date_evaluation else "Date non précisée"
        coef_str = f" (coef {n.coefficient})" if n.coefficient and n.coefficient != 1.0 else ""
        type_str = f" [{n.type_evaluation}]" if n.type_evaluation else ""
        per_label = f" — {n.periode}" if n.periode else ""
        lines.append(f"• **{n.valeur}/20**{coef_str}{type_str} le {d_str}{per_label}")

    lines.append(f"\n[Consulter son bulletin complet](/eleve/{eleve.id})")
    return "\n".join(lines)


def _handle_taux_recouvrement(ecole_id: int, annee_id: Optional[int]) -> str:
    """Calcule avec une précision chirurgicale (100% SQL certifié) le taux de recouvrement global."""
    inscr_q = Inscription.query.filter_by(ecole_id=ecole_id)
    if annee_id:
        inscr_q = inscr_q.filter_by(annee_scolaire_id=annee_id)
    inscriptions = inscr_q.options(joinedload(Inscription.eleve)).all()

    total_attendu = sum((i.eleve.frais_annuels or 0.0) for i in inscriptions if i.eleve)
    total_paye = sum(i.eleve.total_paye() for i in inscriptions if i.eleve)
    reste_total = max(0.0, total_attendu - total_paye)
    taux = round((total_paye / total_attendu * 100.0), 1) if total_attendu > 0 else 0.0

    attendu_fmt = f"{total_attendu:,.0f} FCFA".replace(",", " ")
    paye_fmt = f"{total_paye:,.0f} FCFA".replace(",", " ")
    reste_fmt = f"{reste_total:,.0f} FCFA".replace(",", " ")

    if taux >= 90:
        apprec = "🟢 **Excellent niveau de recouvrement.** La trésorerie est saine."
    elif taux >= 70:
        apprec = "🟡 **Niveau de recouvrement satisfaisant**, des relances restent nécessaires."
    else:
        apprec = "🔴 **Niveau d'arriérés élevé**, une campagne active de relance parentale est recommandée."

    lines = [
        "💳 **Bilan Financier & Taux de Recouvrement Global**\n",
        f"• 📈 **Taux de recouvrement :** **{taux}%**",
        f"• 💰 **Total attendu (scolarité) :** **{attendu_fmt}** ({len(inscriptions)} élève(s))",
        f"• ✅ **Montant déjà encaissé :** **{paye_fmt}**",
        f"• ⚠️ **Reste total à recouvrer :** **{reste_fmt}**\n",
        apprec,
        "\n[Consulter le tableau de bord des paiements](/paiements)"
    ]
    return "\n".join(lines)


def _handle_encaissements_periode(ecole_id: int, mois: Optional[str] = None) -> str:
    """Calcule le volume total encaissé sur le mois en cours certifié en base de données."""
    now = dt.date.today()
    start_month = dt.date(now.year, now.month, 1)

    paiements_mois = (
        Paiement.query.filter_by(ecole_id=ecole_id)
        .options(joinedload(Paiement.eleve))
        .filter(Paiement.date_paiement >= start_month)
        .all()
    )
    total_mois = sum(p.montant for p in paiements_mois if p.montant)
    nb_reglements = len(paiements_mois)

    total_fmt = f"{total_mois:,.0f} FCFA".replace(",", " ")
    mois_nom = now.strftime("%B %Y").capitalize()

    lines = [
        f"💵 **Encaissements du mois ({mois_nom})**\n",
        f"• 💰 **Montant total collecté :** **{total_fmt}**",
        f"• 🧾 **Nombre de règlements enregistrés :** **{nb_reglements} reçu(s)**",
    ]
    if paiements_mois:
        lines.append("\n**Derniers encaissements enregistrés :**")
        for p in sorted(paiements_mois, key=lambda x: x.date_paiement or dt.date.min, reverse=True)[:5]:
            el_nom = f"{p.eleve.prenom} {p.eleve.nom}" if p.eleve else "Élève"
            p_mt = f"{p.montant:,.0f} FCFA".replace(",", " ")
            d_str = p.date_paiement.strftime("%d/%m/%Y") if p.date_paiement else "-"
            lines.append(f"• {d_str} — [{el_nom}](/eleve/{p.eleve_id}) : **{p_mt}** ({p.mode_paiement or 'Espèces'})")

    lines.append("\n[Consulter le journal de caisse](/paiements)")
    return "\n".join(lines)


def _handle_impayes_par_classe(ecole_id: int, annee_id: Optional[int]) -> str:
    """Génère le classement des classes par montant restant dû et retard de scolarité."""
    classes = Classe.query.filter_by(ecole_id=ecole_id)
    if annee_id:
        classes = classes.filter_by(annee_scolaire_id=annee_id)
    all_classes = classes.all()

    stats_classes = []
    for cl in all_classes:
        inscrits = Inscription.query.filter_by(classe_id=cl.id, ecole_id=ecole_id).options(joinedload(Inscription.eleve)).all()
        attendu = sum((i.eleve.frais_annuels or 0.0) for i in inscrits if i.eleve)
        paye = sum(i.eleve.total_paye() for i in inscrits if i.eleve)
        reste = max(0.0, attendu - paye)
        nb_en_retard = sum(1 for i in inscrits if i.eleve and i.eleve.reste_a_payer() > 0)
        taux = round((paye / attendu * 100.0), 1) if attendu > 0 else 100.0
        if reste > 0 or nb_en_retard > 0:
            stats_classes.append({
                "classe_id": cl.id,
                "classe_nom": cl.nom,
                "reste": reste,
                "attendu": attendu,
                "paye": paye,
                "nb_retard": nb_en_retard,
                "total_eleves": len(inscrits),
                "taux": taux
            })

    stats_classes.sort(key=lambda x: x["reste"], reverse=True)

    if not stats_classes:
        return (
            "🎉 **Excellente nouvelle !**\n\n"
            "Toutes les classes sont à 100% à jour de paiement. Aucun arriéré détecté dans votre établissement."
        )

    lines = [
        "📊 **Situation des Impayés par Classe (Classes les plus en retard)**\n"
    ]
    for idx, sc in enumerate(stats_classes[:8], 1):
        reste_fmt = f"{sc['reste']:,.0f} FCFA".replace(",", " ")
        lines.append(
            f"{idx}. **Classe de {sc['classe_nom']}** : **{reste_fmt}** restant dû "
            f"({sc['nb_retard']}/{sc['total_eleves']} élève(s) en retard — Taux : {sc['taux']}%) "
            f"[Voir](/classes/{sc['classe_id']})"
        )

    lines.append("\n[Consulter le suivi financier détaillé](/paiements)")
    return "\n".join(lines)


def _fast_detect_intent_and_entities(question: str, ecole_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Classification heuristique ultra-rapide (< 0.001s) en pur Python :
    Évite un appel réseau lourd à Ollama si la question correspond clairement
    à l'une des requêtes scolaires courantes, aux statistiques ou aux suggestions de l'interface.
    """
    if not question:
        return None

    raw = question.strip()
    q_norm = _normalize_text(raw)

    # 1. Extraction rapide du seuil s'il existe (ex: "plus de 3 absences", "notes supérieures à 15")
    seuil: Optional[int] = None
    m_seuil = re.search(r"(?:plus\s+de|sup[eé]rieur[es]*\s+[aà]|au\s+moins)\s+(\d+)", raw, re.IGNORECASE)
    if m_seuil:
        try:
            seuil = int(m_seuil.group(1))
        except ValueError:
            seuil = None

    # 2. Extraction enrichie de la période (Trimestre 1/2/3, Semestre 1/2) et de l'année ciblée
    periode: Optional[str] = _extract_periode(raw, q_norm)
    matiere: Optional[str] = _extract_matiere(raw, q_norm)
    annee_cible: Optional[int] = _extract_annee_cible(raw, q_norm, ecole_id)

    # 3. Détection de la classe (tolérant aux accents 6e, 6ème, 6eme, etc.)
    classe_nom: Optional[str] = None
    m_classe = re.search(
        r"\b(6e|6eme|5e|5eme|4e|4eme|3e|3eme|2nde|seconde|1ere|1er|premiere|terminale|tle|cp1|cp2|ce1|ce2|cm1|cm2)(?:\s+([a-z]))?\b",
        q_norm,
        re.IGNORECASE
    )
    if m_classe:
        c_base = m_classe.group(1)
        c_sec = m_classe.group(2)
        classe_nom = f"{c_base} {c_sec}".strip().upper() if c_sec else c_base.upper()
    else:
        # Recherche par regex directe sur texte brut si non détecté
        m_classe_raw = re.search(
            r"\b(6[eè]me?|5[eè]me?|4[eè]me?|3[eè]me?|2nde|seconde|1[eè]re?|1er|premi[eè]re?|terminale|tle|cp1|cp2|ce1|ce2|cm1|cm2)(?:\s+([a-zA-Z]))?\b",
            raw,
            re.IGNORECASE
        )
        if m_classe_raw:
            c_base = m_classe_raw.group(1)
            c_sec = m_classe_raw.group(2)
            classe_nom = f"{c_base} {c_sec}".strip().upper() if c_sec else c_base.upper()

    # 4. Fast-Path : Statistiques générales de l'école (sans classe spécifique)
    is_stats_globales = any(k in q_norm for k in (
        "effectif total", "combien d eleves", "combien d eleve", "nombre d eleves", "nombre total d eleves",
        "combien d inscrits", "nombre d inscrits", "combien de classes", "nombre de classes",
        "statistiques", "statistique", "bilan de l ecole", "bilan general", "chiffres cles", "tableau de bord",
        "combien de profs", "combien de professeurs", "nombre de professeurs"
    )) or (q_norm in ("effectif", "effectifs", "statistiques", "stats", "bilan", "effectif ecole", "effectif de l ecole") and not classe_nom)

    if is_stats_globales and not classe_nom:
        return {"intention": "statistiques_globales", "eleve": None, "classe": None, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 4b. Fast-Path Financier Certifié 1 : Taux de recouvrement
    is_taux_recouvrement = any(k in q_norm for k in (
        "taux de recouvrement", "taux recouvrement", "pourcentage de paiement", "pourcentage de recouvrement",
        "pourcentage d encaissement", "niveau de recouvrement", "bilan recouvrement", "etat du recouvrement",
        "ou en sont les paiements", "point sur les paiements"
    ))
    if is_taux_recouvrement:
        return {"intention": "taux_recouvrement", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 4c. Fast-Path Financier Certifié 2 : Encaissements du mois / recettes
    is_encaissements = any(k in q_norm for k in (
        "combien a-t-on encaisse", "combien a t on encaisse", "combien on a encaisse", "total encaisse",
        "recettes du mois", "recette du mois", "caisse du mois", "encaisse ce mois", "paiements du mois",
        "versements du mois", "somme encaissee", "chiffre encaisse"
    ))
    if is_encaissements:
        return {"intention": "encaissements_periode", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 4d. Fast-Path Financier Certifié 3 : Impayés par classe
    is_impayes_par_classe = any(k in q_norm for k in (
        "impayes par classe", "impaye par classe", "retards par classe", "retard par classe",
        "quelle classe a le plus d impayes", "classe avec le plus d impayes", "classe la plus en retard",
        "classes en retard", "retard de paiement par classe", "qui doit le plus par classe"
    ))
    if is_impayes_par_classe:
        return {"intention": "impayes_par_classe", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 5. Fast-Path : Effectif d'une classe spécifique
    if classe_nom and any(k in q_norm for k in ("effectif", "combien", "nombre", "qui est en", "liste des eleves", "liste eleves", "eleves de", "eleve de")):
        return {"intention": "effectif_classe", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 6. Fast-Path : Absences critiques / absentéisme élevé
    is_absences_critiques = any(k in q_norm for k in (
        "qui a le plus d absences", "plus d absences", "plus d absence", "eleves souvent absents",
        "absences critiques", "taux d absence eleve", "qui est le plus absent", "les plus absents",
        "pire absence", "fort absenteisme", "fort absentéisme", "record d absences", "beaucoup d absences"
    ))
    if is_absences_critiques:
        return {"intention": "absences_critiques", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 7. Fast-Path : Meilleurs élèves / Tableau d'honneur
    is_meilleurs_eleves = any(k in q_norm for k in (
        "meilleurs eleves", "meilleur eleve", "meilleure moyenne", "meilleures moyennes",
        "qui est premier", "qui est le premier", "premiers de la classe", "major de promotion",
        "major", "tableau d honneur", "felicitations", "qui a la meilleure note", "meilleures notes"
    ))
    if is_meilleurs_eleves:
        return {"intention": "meilleurs_eleves", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 8. Fast-Path : Corps enseignant / Liste des professeurs
    is_professeurs = any(k in q_norm for k in (
        "liste des professeurs", "liste des profs", "qui sont les professeurs", "qui sont les profs",
        "enseignants", "corps enseignant", "qui enseigne", "les professeurs", "les profs"
    ))
    if is_professeurs:
        return {"intention": "professeurs_cours", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 9. Fast-Path : Situation des impayés et frais de scolarité
    is_impayes = any(k in q_norm for k in (
        "qui a des impayes", "qui a des impaye", "impayes", "impaye", "qui doit payer",
        "reste a payer", "retard de paiement", "retards de paiement", "situation financiere",
        "frais de scolarite", "qui n a pas paye la scolarite", "scolarite impayee", "point financier"
    ))
    if is_impayes and not any(k in q_norm for k in ("rentree", "reinscription", "preinscrit")):
        return {"intention": "impayes_scolarite", "eleve": None, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # 10. Détection de l'élève par regex explicite
    eleve_nom: Optional[str] = None
    eleve_patterns = [
        r"(?:qui\s+est\s+(?:l['’]élève\s+|l['’]eleve\s+|ce\s+|cet\s+|cette\s+)?|c['’]est\s+qui\s+|connais-tu\s+|tu\s+connais\s+|parle-moi\s+de\s+|parlez-moi\s+de\s+|qu['’]en\s+est-il\s+de\s+|infos?\s+sur\s+|renseigne-moi\s+sur\s+|dis-moi\s+sur\s+)([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
        r"(?:l['’]élève|l['’]eleve|eleve|élève)\s+([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
        r"(?:fiche|dossier|bulletin|notes?|absences?|coordonnées|contact)\s+(?:de\s+|d['’]\s*)([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
        r"(?:pour|sur|concernant)\s+([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
    ]
    for pat in eleve_patterns:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            cand = _clean_search_term(m.group(1))
            if cand and cand.lower() not in (
                "la", "cette", "tous", "toutes", "chaque", "classe", "ecole", "école",
                "son", "ses", "des", "une", "un", "mon", "notre", "leurs", "lui",
                "null", "none", "inconnu", "undefined", "nil", "n/a", "critiques", "tout", "toutes", "complete", "complète", "qui", "ce",
                "en", "en maths", "en svt", "en francais", "maths", "svt", "francais"
            ) and not cand.lower().startswith("en ") and not _extract_matiere(cand, _normalize_text(cand)):
                eleve_nom = cand
                break

    # Mots-clés pour fiche élève
    is_fiche = any(k in q_norm for k in ("fiche", "dossier", "information", "informations", "info", "infos", "profil", "tout savoir", "qui est", "c est qui", "connais tu", "parle moi", "statut de"))

    # Si aucun nom n'est extrait mais que l'utilisateur a tapé uniquement 1 à 3 mots (ex: 'Mamadou' ou 'Mamadou Sow')
    # GARDE-FOU ESSENTIEL : Ne déduire un élève que s'il existe VRAIMENT dans la base de l'établissement
    if not eleve_nom and not classe_nom:
        words = raw.split()
        if 1 <= len(words) <= 3 and all(w.replace("-", "").isalpha() for w in words):
            cand_clean = _clean_search_term(raw)
            if cand_clean and cand_clean.lower() not in (
                "bonjour", "bonjours", "bonsoir", "salut", "coucou", "hello", "hi", "merci",
                "aide", "test", "classe", "ecole", "note", "notes", "absence", "absences",
                "oui", "non", "null", "none", "inconnu", "undefined", "qui", "quoi", "comment",
                "super", "ok", "cool", "d accord", "remercie"
            ):
                # On vérifie si un élève correspond dans la base avant de supposer qu'il s'agit d'un nom d'élève
                if ecole_id:
                    matched = _search_eleves_in_ecole(cand_clean, ecole_id)
                    if matched:
                        eleve_nom = cand_clean
                        is_fiche = True
                else:
                    # En environnement sans ecole_id (ex: certains tests unitaires isolés)
                    eleve_nom = cand_clean
                    is_fiche = True

    # Mots-clés pour contact
    is_contact = any(k in q_norm for k in ("contact", "contacts", "coordonnee", "coordonnees", "parent", "parents", "telephone", "telephones", "tel", "numero", "numeros", "email", "mail", "joindre", "appeler"))
    # Mots-clés pour absences
    is_absences = any(k in q_norm for k in ("absence", "absences", "absent", "absents", "assiduite"))
    # Mots-clés pour notes
    is_notes = any(k in q_norm for k in ("note", "notes", "moyenne", "moyennes", "bulletin", "bulletins", "evaluation", "evaluations", "resultat", "resultats"))

    # Précision par matière scolaire (ex: "note en maths", "moyenne en svt de Fatou")
    if matiere and (is_notes or eleve_nom or any(k in q_norm for k in ("note", "notes", "moyenne", "moyennes", "combien a", "evaluation"))):
        return {"intention": "notes_matiere", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

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
        return {"intention": "relance_reinscriptions", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}
    if is_statut_rentree:
        return {"intention": "statut_rentree", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}
    if is_fiche or (is_notes and is_absences):
        return {"intention": "fiche_eleve", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}
    if is_contact and not is_notes and not is_absences:
        return {"intention": "contact_parent", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}
    if is_absences and not is_notes:
        return {"intention": "absences", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}
    if is_notes and not is_absences:
        return {"intention": "notes", "eleve": eleve_nom, "classe": classe_nom, "periode": periode, "seuil": seuil, "matiere": matiere, "annee_cible": annee_cible}

    # Si rien de net n'est détecté, on laisse la main au parseur Ollama
    return None


def _handle_statistiques_globales(ecole_id: int, annee_id: Optional[int]) -> str:
    """Génère instantanément (< 20ms) la synthèse statistique clé de l'école."""
    inscr_q = Inscription.query.filter_by(ecole_id=ecole_id)
    if annee_id:
        inscr_q = inscr_q.filter_by(annee_scolaire_id=annee_id)

    total_inscrits = inscr_q.count()
    garcons = (
        inscr_q.join(Eleve, Eleve.id == Inscription.eleve_id)
        .filter(Eleve.genre.in_(("M", "Garçon", "Masculin")))
        .count()
    )
    filles = max(0, total_inscrits - garcons)

    classes_q = Classe.query.filter_by(ecole_id=ecole_id)
    if annee_id:
        classes_q = classes_q.filter_by(annee_scolaire_id=annee_id)
    total_classes = classes_q.count()

    profs_count = Utilisateur.query.filter_by(ecole_id=ecole_id, role="professeur", statut="actif").count()
    if profs_count == 0:
        profs_count = db.session.query(Cours.professeur_id).filter_by(ecole_id=ecole_id).filter(Cours.professeur_id != None).distinct().count()

    abs_q = Absence.query.filter_by(ecole_id=ecole_id)
    if annee_id:
        abs_q = abs_q.join(
            Inscription,
            or_(
                Absence.inscription_id == Inscription.id,
                Absence.eleve_id == Inscription.eleve_id
            )
        ).filter(Inscription.annee_scolaire_id == annee_id)
    total_abs = abs_q.count()
    just = abs_q.filter(Absence.justifiee == True).count()
    non_just = total_abs - just

    lines = [
        "📊 **Tableau de Bord & Statistiques de l'Établissement**\n",
        f"• 👥 **Effectif total :** **{total_inscrits} élève(s)** ({garcons} garçon(s), {filles} fille(s))",
        f"• 🏫 **Classes ouvertes :** **{total_classes} classe(s)**",
        f"• 👨‍🏫 **Corps enseignant :** **{profs_count} professeur(s)**",
        f"• ⏱️ **Assiduité :** **{total_abs} absence(s)** au total ({just} justifiée(s), {non_just} non justifiée(s))",
        "\n[Consulter les classes](/classes) • [Consulter la liste des élèves](/eleves)"
    ]
    return "\n".join(lines)


def _handle_effectif_classe(ecole_id: int, annee_id: Optional[int], classe_nom: str) -> str:
    """Génère instantanément (< 20ms) l'effectif et la composition d'une classe."""
    cl_query = Classe.query.filter(Classe.ecole_id == ecole_id)
    if annee_id:
        cl_query = cl_query.filter(Classe.annee_scolaire_id == annee_id)

    classe = cl_query.filter(Classe.nom.ilike(f"%{classe_nom}%")).first()
    if not classe:
        classe = Classe.query.filter(Classe.ecole_id == ecole_id, Classe.nom.ilike(f"%{classe_nom}%")).first()

    # Fallback normalisé (6ème A vs 6eme a)
    if not classe:
        all_cls = cl_query.all() or Classe.query.filter_by(ecole_id=ecole_id).all()
        norm_target = _normalize_text(classe_nom).replace(" ", "")
        for c in all_cls:
            c_norm = _normalize_text(c.nom).replace(" ", "")
            if norm_target in c_norm or c_norm in norm_target:
                classe = c
                break

    if not classe:
        avail_classes = Classe.query.filter_by(ecole_id=ecole_id).order_by(Classe.nom.asc()).limit(10).all()
        c_names = ", ".join(f"**{c.nom}**" for c in avail_classes) if avail_classes else "Aucune classe trouvée"
        return (
            f"Je n'ai pas trouvé de classe correspondant à *{classe_nom}*.\n\n"
            f"Classes existantes : {c_names}.\n\n"
            f"[Consulter la liste des classes](/classes)"
        )

    inscrits = (
        Inscription.query.filter_by(classe_id=classe.id, ecole_id=ecole_id)
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .options(joinedload(Inscription.eleve))
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )
    total = len(inscrits)
    garcons = sum(1 for i in inscrits if (i.eleve.genre or "").upper() in ("M", "GARÇON", "GARCON", "MASCULIN"))
    filles = max(0, total - garcons)

    lines = [
        f"👥 **Effectif de la classe {classe.nom}** ({classe.niveau or 'Niveau standard'})\n",
        f"• **Effectif total :** **{total} élève(s)** ({garcons} garçon(s), {filles} fille(s))",
    ]
    if classe.salle:
        lines.append(f"• **Salle de cours :** {classe.salle}")

    if inscrits:
        lines.append("\n**Liste des élèves inscrits :**")
        for idx, insc in enumerate(inscrits[:25], 1):
            el = insc.eleve
            mat = el.code_parent or f"ELV-{el.id}"
            lines.append(f"{idx}. [{el.prenom} {el.nom}](/eleve/{el.id}) (Matricule: `{mat}`)")
        if total > 25:
            lines.append(f"\n*... et {total - 25} autre(s) élève(s).*")

    lines.append(f"\n[Voir la fiche classe](/classes/{classe.id}) • [Emploi du temps](/emploi/classe/{classe.id}/imprimer)")
    return "\n".join(lines)


def _handle_absences_critiques(ecole_id: int, annee_id: Optional[int], classe_nom: Optional[str] = None) -> str:
    """Génère instantanément le palmarès des élèves ayant le plus d'absences."""
    query = (
        db.session.query(
            Eleve.id.label("eleve_id"),
            Eleve.nom,
            Eleve.prenom,
            Classe.id.label("classe_id"),
            Classe.nom.label("classe_nom"),
            func.count(Absence.id).label("total_absences"),
            func.sum(db.case((Absence.justifiee == False, 1), else_=0)).label("non_justifiees")
        )
        .join(Inscription, Inscription.eleve_id == Eleve.id)
        .join(Classe, Classe.id == Inscription.classe_id)
        .join(Absence, or_(Absence.inscription_id == Inscription.id, Absence.eleve_id == Eleve.id))
        .filter(Inscription.ecole_id == ecole_id)
    )
    if annee_id:
        query = query.filter(Inscription.annee_scolaire_id == annee_id)
    if classe_nom:
        query = query.filter(Classe.nom.ilike(f"%{classe_nom}%"))

    results = (
        query.group_by(Eleve.id, Eleve.nom, Eleve.prenom, Classe.id, Classe.nom)
        .order_by(func.sum(db.case((Absence.justifiee == False, 1), else_=0)).desc(), func.count(Absence.id).desc())
        .limit(10)
        .all()
    )

    if not results:
        return (
            "🎉 **Assiduité exemplaire !**\n\n"
            "Aucune absence critique n'a été signalée pour l'instant dans votre établissement."
        )

    lines = [f"⚠️ **Élèves présentant le plus d'absences {f'({classe_nom})' if classe_nom else ''}**\n"]
    for idx, r in enumerate(results, 1):
        non_j = int(r.non_justifiees or 0)
        total_a = int(r.total_absences or 0)
        lines.append(
            f"{idx}. [{r.prenom} {r.nom}](/eleve/{r.eleve_id}) ({r.classe_nom}) : "
            f"**{total_a} absence(s)** (dont **{non_j} non justifiée(s)**)"
        )

    lines.append("\n[Consulter le registre complet des absences](/absences)")
    return "\n".join(lines)


def _handle_meilleurs_eleves(ecole_id: int, annee_id: Optional[int], classe_nom: Optional[str] = None) -> str:
    """Génère instantanément le tableau d'honneur des meilleures moyennes."""
    query = (
        db.session.query(
            Eleve.id.label("eleve_id"),
            Eleve.nom,
            Eleve.prenom,
            Classe.id.label("classe_id"),
            Classe.nom.label("classe_nom"),
            func.round(func.avg(Note.valeur), 2).label("moyenne"),
            func.count(Note.id).label("nb_notes")
        )
        .join(Inscription, Inscription.eleve_id == Eleve.id)
        .join(Classe, Classe.id == Inscription.classe_id)
        .join(Note, or_(Note.inscription_id == Inscription.id, Note.eleve_id == Eleve.id))
        .filter(Inscription.ecole_id == ecole_id)
    )
    if annee_id:
        query = query.filter(Inscription.annee_scolaire_id == annee_id)
    if classe_nom:
        query = query.filter(Classe.nom.ilike(f"%{classe_nom}%"))

    results = (
        query.group_by(Eleve.id, Eleve.nom, Eleve.prenom, Classe.id, Classe.nom)
        .having(func.count(Note.id) >= 1)
        .order_by(func.avg(Note.valeur).desc())
        .limit(10)
        .all()
    )

    if not results:
        return (
            "ℹ️ **Résultats scolaires**\n\n"
            "Aucune évaluation notée n'a encore été enregistrée pour établir un classement."
        )

    medailles = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 **Tableau d'Honneur — Meilleurs Élèves {f'({classe_nom})' if classe_nom else ''}**\n"]
    for idx, r in enumerate(results, 1):
        badge = medailles[idx - 1] if idx <= 3 else f"{idx}."
        lines.append(
            f"{badge} [{r.prenom} {r.nom}](/eleve/{r.eleve_id}) ({r.classe_nom}) : "
            f"**{r.moyenne}/20** ({r.nb_notes} évaluation(s))"
        )

    lines.append("\n[Consulter la saisie des notes](/saisie_notes_classe)")
    return "\n".join(lines)


def _handle_professeurs_cours(ecole_id: int) -> str:
    """Génère instantanément le répertoire des professeurs et matières."""
    profs = (
        Utilisateur.query.filter_by(ecole_id=ecole_id, role="professeur", statut="actif")
        .order_by(Utilisateur.nom.asc(), Utilisateur.prenom.asc())
        .all()
    )

    if not profs:
        return (
            "ℹ️ **Corps Enseignant**\n\n"
            "Aucun compte professeur actif n'a été trouvé dans votre établissement.\n\n"
            "[Gérer les professeurs](/utilisateurs)"
        )

    lines = [f"👨‍🏫 **Corps Enseignant ({len(profs)} professeur(s))**\n"]
    for p in profs:
        cours_assignes = Cours.query.filter_by(professeur_id=p.id, ecole_id=ecole_id).all()
        matieres = ", ".join(sorted(set(c.nom for c in cours_assignes))) if cours_assignes else "Aucun cours assigné"
        contact = f"`{p.telephone}`" if p.telephone else p.email
        lines.append(f"• **{p.prenom} {p.nom}** — *{matieres}* ({contact})")

    lines.append("\n[Consulter l'annuaire des utilisateurs](/utilisateurs)")
    return "\n".join(lines)


def _handle_impayes_scolarite(ecole_id: int, annee_id: Optional[int]) -> str:
    """Génère instantanément le bilan des impayés et retards de scolarité."""
    inscriptions = (
        Inscription.query.filter_by(ecole_id=ecole_id)
        .options(joinedload(Inscription.eleve), joinedload(Inscription.classe))
        .all()
    )
    if annee_id:
        inscriptions = [i for i in inscriptions if i.annee_scolaire_id == annee_id]

    impayes = []
    for insc in inscriptions:
        el = insc.eleve
        if el:
            reste = el.reste_a_payer()
            if reste and reste > 0:
                impayes.append({
                    "eleve_id": el.id,
                    "nom": f"{el.prenom} {el.nom}",
                    "classe": insc.classe.nom if insc.classe else "Sans classe",
                    "reste": reste,
                    "frais": el.frais_annuels or 0,
                    "tel": el.contact_parent or el.telephone or "Non renseigné",
                })

    impayes.sort(key=lambda x: x["reste"], reverse=True)

    if not impayes:
        return (
            "🎉 **Scolarité à jour !**\n\n"
            "Aucun arriéré de scolarité n'a été détecté dans votre établissement. "
            "Toutes les inscriptions sont en règle."
        )

    total_du = sum(item["reste"] for item in impayes)
    total_du_str = f"{total_du:,.0f} FCFA".replace(",", " ")
    lines = [
        f"💳 **Situation des Impayés de Scolarité**\n",
        f"• **Dossiers concernés :** **{len(impayes)} élève(s)**",
        f"• **Montant total restant dû :** **{total_du_str}**\n",
        "**Soldes principaux en attente :**"
    ]
    for idx, item in enumerate(impayes[:10], 1):
        reste_fmt = f"{item['reste']:,.0f} FCFA".replace(",", " ")
        lines.append(
            f"{idx}. [{item['nom']}](/eleve/{item['eleve_id']}) ({item['classe']}) : "
            f"**{reste_fmt}** — Tél: `{item['tel']}`"
        )

    if len(impayes) > 10:
        lines.append(f"\n*... et {len(impayes) - 10} autre(s) élève(s) avec solde restant.*")

    lines.append("\n[Consulter le suivi financier des paiements](/paiements)")
    return "\n".join(lines)


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


@assistant_bp.route("/stream", methods=["POST"])
@login_required
@role_required("admin", "directeur", "super_admin")
def api_assistant_stream():
    """
    POST /api/assistant/stream
    Streaming mot-à-mot (Server-Sent Events — SSE) :
    - Fast-Path (Salutations, Stats, Absences, Classes, Fiches directes) : émet la réponse complète en 1 événement SSE direct (< 50ms).
    - Mode Analytique / Rédaction longue via Ollama : émet les tokens mot-à-mot via stream_assistant.
    Format SSE : data: {"chunk": "...", "done": false}\n\ndata: {"done": true}\n\n
    """
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or payload.get("message") or "").strip()
    history = payload.get("history") or []

    def sse_event(chunk: str, done: bool = False) -> str:
        return f"data: {json.dumps({'chunk': chunk, 'done': done}, ensure_ascii=False)}\n\n"

    if not question:
        def empty_stream():
            yield sse_event("⚠️ Le champ question est requis.", done=True)
        return Response(empty_stream(), mimetype="text/event-stream")

    ecole_id = getattr(current_user, "ecole_id", None)
    annee_active = get_annee_active(ecole_id) if ecole_id else None
    annee_id = annee_active.id if annee_active else None

    # 1. Chitchat / Salutations directes
    chitchat = _get_chitchat_response(question)
    if chitchat:
        def chitchat_stream():
            yield sse_event(chitchat, done=True)
        return Response(chitchat_stream(), mimetype="text/event-stream")

    # 2. Fast-Path Intent Detection
    intent = _fast_detect_intent_and_entities(question, ecole_id=ecole_id)
    intention = intent.get("intention") if intent else "autre"
    classe_param = intent.get("classe") if intent else None
    eleve_param = intent.get("eleve") if intent else None
    matiere_param = intent.get("matiere") if intent else None
    periode_param = intent.get("periode") if intent else None
    annee_cible = intent.get("annee_cible") if intent else None
    target_annee_id = annee_cible or annee_id

    # Résolution anaphore si matière ou question de suivi sans élève explicite
    last_eleve_id = session.get("ai_last_eleve_id")
    if not eleve_param and last_eleve_id and (intention in ("notes_matiere", "fiche_eleve", "notes", "absences", "contact_parent") or matiere_param):
        cached = Eleve.query.filter_by(id=last_eleve_id, ecole_id=ecole_id).first()
        if cached:
            eleve_param = f"{cached.prenom} {cached.nom}"
            if matiere_param:
                intention = "notes_matiere"

    matched: List[Eleve] = []
    if eleve_param and ecole_id:
        matched = _search_eleves_in_ecole(eleve_param, ecole_id)
        if classe_param and matched:
            c_norm = _normalize_text(classe_param)
            filtered = [
                el for el in matched
                if any(i.classe and (c_norm in _normalize_text(i.classe.nom) or _normalize_text(i.classe.nom) in c_norm) for i in el.inscriptions)
            ]
            if filtered:
                matched = filtered

        # Gestion chirurgicale des homonymes (levée d'ambiguïté avec boutons interactifs)
        if len(matched) > 1 and intention in ("fiche_eleve", "notes", "absences", "contact_parent", "notes_matiere"):
            choice_reply = _format_homonymes_choice(matched, eleve_param, ecole_id)
            def homonym_stream():
                yield sse_event(choice_reply, done=True)
            return Response(homonym_stream(), mimetype="text/event-stream")

    fast_reply = None
    if intention == "statistiques_globales" and ecole_id:
        fast_reply = _handle_statistiques_globales(ecole_id, target_annee_id)
    elif intention == "taux_recouvrement" and ecole_id:
        fast_reply = _handle_taux_recouvrement(ecole_id, target_annee_id)
    elif intention == "encaissements_periode" and ecole_id:
        fast_reply = _handle_encaissements_periode(ecole_id)
    elif intention == "impayes_par_classe" and ecole_id:
        fast_reply = _handle_impayes_par_classe(ecole_id, target_annee_id)
    elif intention == "effectif_classe" and ecole_id and classe_param:
        fast_reply = _handle_effectif_classe(ecole_id, target_annee_id, classe_param)
    elif intention == "absences_critiques" and ecole_id:
        fast_reply = _handle_absences_critiques(ecole_id, target_annee_id, classe_param)
    elif intention == "meilleurs_eleves" and ecole_id:
        fast_reply = _handle_meilleurs_eleves(ecole_id, target_annee_id, classe_param)
    elif intention == "professeurs_cours" and ecole_id:
        fast_reply = _handle_professeurs_cours(ecole_id)
    elif intention == "impayes_scolarite" and ecole_id:
        fast_reply = _handle_impayes_scolarite(ecole_id, target_annee_id)
    elif intention == "notes_matiere" and matched and ecole_id:
        target_el = matched[0]
        session["ai_last_eleve_id"] = target_el.id
        session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
        session.modified = True
        fast_reply = _handle_notes_matiere(target_el, matiere_param or "Mathématiques", ecole_id, target_annee_id, periode_param)
    elif intention == "notes_matiere" and not eleve_param:
        fast_reply = f"Pour quel élève souhaitez-vous consulter les résultats en **{matiere_param or 'cette matière'}** ? Vous pouvez préciser son nom ou sa classe."
    elif intention == "fiche_eleve" and matched and ecole_id:
        target_el = matched[0]
        dossier = _build_eleve_dossier(target_el, ecole_id, target_annee_id, periode_param)
        session["ai_last_eleve_id"] = target_el.id
        session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
        session.modified = True
        fast_reply = _format_fiche_eleve_markdown(dossier)
    elif intention == "notes" and matched and ecole_id:
        target_el = matched[0]
        dossier = _build_eleve_dossier(target_el, ecole_id, target_annee_id, periode_param)
        session["ai_last_eleve_id"] = target_el.id
        session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
        session.modified = True
        fast_reply = _format_notes_eleve_markdown(dossier, periode_param)
    elif intention == "absences" and matched and ecole_id:
        target_el = matched[0]
        dossier = _build_eleve_dossier(target_el, ecole_id, target_annee_id, periode_param)
        session["ai_last_eleve_id"] = target_el.id
        session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
        session.modified = True
        fast_reply = _format_absences_eleve_markdown(dossier)
    elif intention == "contact_parent" and matched and ecole_id:
        target_el = matched[0]
        dossier = _build_eleve_dossier(target_el, ecole_id, target_annee_id, periode_param)
        session["ai_last_eleve_id"] = target_el.id
        session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
        session.modified = True
        fast_reply = _format_contact_parent_markdown(dossier)
    elif intention in ("fiche_eleve", "notes_matiere", "notes", "absences", "contact_parent") and eleve_param and not matched:
        fast_reply = f"🔍 Aucun élève correspondant à « **{eleve_param}** » n'a été trouvé parmi les inscriptions actives de votre établissement.\n\n[Consulter la liste des élèves](/eleves)"

    if fast_reply:
        def fast_stream():
            yield sse_event(fast_reply, done=True)
        return Response(fast_stream(), mimetype="text/event-stream")

    # 3. Mode Streaming IA (Server-Sent Events) via Ollama
    def sse_generator():
        try:
            tokens_stream = stream_assistant(question, system_context=SYSTEM_ASSISTANT, history=history)
            for token in tokens_stream:
                if token:
                    yield sse_event(token, done=False)
            yield sse_event("", done=True)
        except Exception as exc:
            logger.error("Erreur dans sse_generator : %s", exc)
            yield sse_event(f"⚠️ Erreur lors de la génération continue : {exc}", done=True)

    return Response(stream_with_context(sse_generator()), mimetype="text/event-stream")


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
    fast_intent = _fast_detect_intent_and_entities(question, ecole_id=ecole_id)
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
            r"(?:fiche|dossier|bulletin|notes?|absences?)\s+(?:de\s+|d['’]\s*)([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)",
            r"(?:pour|sur)\s+([A-Za-zÀ-ÿ\-]+(?:\s+[A-Za-zÀ-ÿ\-]+)?)"
        ]
        for pat in patterns:
            m = re.search(pat, question, re.IGNORECASE)
            if m:
                cand = _clean_search_term(m.group(1))
                if cand and cand.lower() not in (
                    "la", "cette", "tous", "toutes", "chaque", "classe", "ecole", "école",
                    "son", "ses", "des", "une", "un", "mon", "notre", "leurs", "lui",
                    "null", "none", "inconnu", "undefined", "nil", "n/a", "qui", "ce",
                    "en", "en maths", "en svt", "en francais", "maths", "svt", "francais"
                ) and not cand.lower().startswith("en ") and not _extract_matiere(cand, _normalize_text(cand)):
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

    matiere_param = intent.get("matiere")
    annee_cible = intent.get("annee_cible")
    target_annee_id = annee_cible or annee_id

    # 4. Mémoire d'entité en session Flask (Résolution d'anaphores)
    has_anaphora = any(re.search(pat, q_lower) for pat in ANAPHORA_PATTERNS)
    last_eleve_id = session.get("ai_last_eleve_id")

    if not eleve_param and last_eleve_id and (has_anaphora or intention in ("fiche_eleve", "notes", "absences", "contact_parent", "notes_matiere") or matiere_param):
        cached_eleve = Eleve.query.filter_by(id=last_eleve_id, ecole_id=ecole_id).first()
        if cached_eleve:
            eleve_param = f"{cached_eleve.prenom} {cached_eleve.nom}"
            logger.info("Anaphore résolue via session : élève ID %s (%s)", cached_eleve.id, eleve_param)

            # Raffiner l'intention selon les mots-clés de la question de suivi
            if matiere_param:
                intention = "notes_matiere"
            elif "absence" in q_lower:
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

    matched_eleves: List[Eleve] = []
    if eleve_param and ecole_id:
        matched_eleves = _search_eleves_in_ecole(eleve_param, ecole_id)
        if classe_param and matched_eleves:
            c_norm = _normalize_text(classe_param)
            filtered = [
                el for el in matched_eleves
                if any(i.classe and (c_norm in _normalize_text(i.classe.nom) or _normalize_text(i.classe.nom) in c_norm) for i in el.inscriptions)
            ]
            if filtered:
                matched_eleves = filtered

        # Gestion chirurgicale des homonymes (levée d'ambiguïté sans erreur)
        if len(matched_eleves) > 1 and intention in ("fiche_eleve", "notes", "absences", "contact_parent", "notes_matiere"):
            choice_reply = _format_homonymes_choice(matched_eleves, eleve_param, ecole_id)
            return jsonify({
                "success": True,
                "intention": "homonymes_detectes",
                "criteres": intent,
                "donnees_trouvees": len(matched_eleves),
                "donnees": [],
                "reply": choice_reply,
            })

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

        if matiere_param:
            intention = "notes_matiere"
        elif (has_note and has_abs) or has_fiche or intention == "fiche_eleve":
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

        # FAST-PATH 1 : STATISTIQUES GÉNÉRALES
        elif intention == "statistiques_globales" and ecole_id:
            reply = _handle_statistiques_globales(ecole_id, annee_id)
            return jsonify({
                "success": True,
                "intention": "statistiques_globales",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 2 : EFFECTIF D'UNE CLASSE
        elif intention == "effectif_classe" and ecole_id and classe_param:
            reply = _handle_effectif_classe(ecole_id, annee_id, classe_param)
            return jsonify({
                "success": True,
                "intention": "effectif_classe",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 3 : ABSENCES CRITIQUES
        elif intention == "absences_critiques" and ecole_id:
            reply = _handle_absences_critiques(ecole_id, annee_id, classe_param)
            return jsonify({
                "success": True,
                "intention": "absences_critiques",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 4 : MEILLEURS ÉLÈVES / TABLEAU D'HONNEUR
        elif intention == "meilleurs_eleves" and ecole_id:
            reply = _handle_meilleurs_eleves(ecole_id, annee_id, classe_param)
            return jsonify({
                "success": True,
                "intention": "meilleurs_eleves",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 5 : LISTE DES PROFESSEURS
        elif intention == "professeurs_cours" and ecole_id:
            reply = _handle_professeurs_cours(ecole_id)
            return jsonify({
                "success": True,
                "intention": "professeurs_cours",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 6 : IMPAYÉS ET RESTE À PAYER
        elif intention == "impayes_scolarite" and ecole_id:
            reply = _handle_impayes_scolarite(ecole_id, target_annee_id)
            return jsonify({
                "success": True,
                "intention": "impayes_scolarite",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 7 : TAUX DE RECOUVREMENT (100% SQL CERTIFIÉ)
        elif intention == "taux_recouvrement" and ecole_id:
            reply = _handle_taux_recouvrement(ecole_id, target_annee_id)
            return jsonify({
                "success": True,
                "intention": "taux_recouvrement",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 8 : ENCAISSEMENTS DU MOIS / CAISSE
        elif intention == "encaissements_periode" and ecole_id:
            reply = _handle_encaissements_periode(ecole_id)
            return jsonify({
                "success": True,
                "intention": "encaissements_periode",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 9 : IMPAYÉS PAR CLASSE (CLASSEMENT DETTES)
        elif intention == "impayes_par_classe" and ecole_id:
            reply = _handle_impayes_par_classe(ecole_id, target_annee_id)
            return jsonify({
                "success": True,
                "intention": "impayes_par_classe",
                "criteres": intent,
                "donnees_trouvees": 1,
                "donnees": [],
                "reply": reply,
            })

        # FAST-PATH 10 : NOTES PAR MATIÈRE SPÉCIFIQUE
        elif intention == "notes_matiere":
            if eleve_param and not matched_eleves:
                reply = f"🔍 Aucun élève correspondant à « **{eleve_param}** » n'a été trouvé parmi les inscriptions actives de votre établissement.\n\n[Consulter la liste des élèves](/eleves)"
                return jsonify({
                    "success": True,
                    "intention": "notes_matiere",
                    "criteres": intent,
                    "donnees_trouvees": 0,
                    "donnees": [],
                    "reply": reply,
                })
            elif not eleve_param:
                reply = f"Pour quel élève souhaitez-vous consulter les résultats en **{matiere_param or 'cette matière'}** ? Vous pouvez préciser son nom ou sa classe."
                return jsonify({
                    "success": True,
                    "intention": "notes_matiere",
                    "criteres": intent,
                    "donnees_trouvees": 0,
                    "donnees": [],
                    "reply": reply,
                })
            elif matched_eleves and ecole_id:
                target_el = matched_eleves[0]
                session["ai_last_eleve_id"] = target_el.id
                session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                session.modified = True
                reply = _handle_notes_matiere(target_el, matiere_param or "Mathématiques", ecole_id, target_annee_id, periode_param)
                return jsonify({
                    "success": True,
                    "intention": "notes_matiere",
                    "criteres": intent,
                    "donnees_trouvees": 1,
                    "donnees": [],
                    "reply": reply,
                })

        # A) FICHE COMPLÈTE DE L'ÉLÈVE
        elif intention == "fiche_eleve" and eleve_param:
            matched_eleves = _search_eleves_in_ecole(eleve_param, ecole_id) if ecole_id else []

            if not matched_eleves:
                reply = f"🔍 Aucun élève correspondant à « **{eleve_param}** » n'a été trouvé parmi les inscriptions actives de votre établissement.\n\n[Consulter la liste des élèves](/eleves)"
                return jsonify({
                    "success": True,
                    "intention": "fiche_eleve",
                    "criteres": intent,
                    "donnees_trouvees": 0,
                    "donnees": [],
                    "reply": reply,
                })
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
                    reply = f"🔍 Aucun élève correspondant à « **{eleve_param}** » n'a été trouvé parmi les inscriptions de votre établissement.\n\n[Consulter la liste des élèves](/eleves)"
                    return jsonify({
                        "success": True,
                        "intention": "absences",
                        "criteres": intent,
                        "donnees_trouvees": 0,
                        "donnees": [],
                        "reply": reply,
                    })
                else:
                    target_el = matched_eleves[0]
                    session["ai_last_eleve_id"] = target_el.id
                    session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                    session.modified = True

                    for el in matched_eleves:
                        dossier = _build_eleve_dossier(el, ecole_id, target_annee_id, periode_param)
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

                if target_annee_id:
                    query = query.filter(Inscription.annee_scolaire_id == target_annee_id)
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
                    reply = f"🔍 Aucun élève correspondant à « **{eleve_param}** » n'a été trouvé parmi les inscriptions de votre établissement.\n\n[Consulter la liste des élèves](/eleves)"
                    return jsonify({
                        "success": True,
                        "intention": "notes",
                        "criteres": intent,
                        "donnees_trouvees": 0,
                        "donnees": [],
                        "reply": reply,
                    })
                else:
                    target_el = matched_eleves[0]
                    session["ai_last_eleve_id"] = target_el.id
                    session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                    session.modified = True

                    for el in matched_eleves:
                        dossier = _build_eleve_dossier(el, ecole_id, target_annee_id, periode_param)
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

                if target_annee_id:
                    query = query.filter(Inscription.annee_scolaire_id == target_annee_id)
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
                    reply = f"🔍 Aucun élève correspondant à « **{eleve_param}** » n'a été trouvé parmi les inscriptions de votre établissement.\n\n[Consulter la liste des élèves](/eleves)"
                    return jsonify({
                        "success": True,
                        "intention": "contact_parent",
                        "criteres": intent,
                        "donnees_trouvees": 0,
                        "donnees": [],
                        "reply": reply,
                    })
                else:
                    target_el = matched_eleves[0]
                    session["ai_last_eleve_id"] = target_el.id
                    session["ai_last_eleve_nom"] = f"{target_el.prenom} {target_el.nom}"
                    session.modified = True

                    for el in matched_eleves:
                        dossier = _build_eleve_dossier(el, ecole_id, target_annee_id, periode_param)
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

                if target_annee_id:
                    query = query.filter(Inscription.annee_scolaire_id == target_annee_id)
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
    elif intention == "notes" and records_summary and not is_analytical and eleve_param:
        final_reply = _format_notes_eleve_markdown(records_summary[0], periode_param)
        if len(records_summary) > 1:
            noms_autres = ", ".join(f"{r['nom_complet']} ({r['classe']})" for r in records_summary[1:3])
            final_reply += f"\n\n*Note : D'autres élèves correspondent également : {noms_autres}.*"
    elif intention == "absences" and records_summary and not is_analytical and eleve_param:
        final_reply = _format_absences_eleve_markdown(records_summary[0])
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
