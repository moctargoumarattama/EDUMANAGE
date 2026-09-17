import re
from datetime import date
from flask import session, g
from sqlalchemy.orm import joinedload

from app.models import AnneeScolaire, Classe, Eleve, Inscription
from app.services.coherence_temporelle import (
    MESSAGE_DATE_FIN_APRES_DEBUT,
    valider_intervalle_dates,
)

MESSAGE_ANNEE_COURTE_INVALIDE = "Entrez une année scolaire valide, par exemple 2026-2027."
MESSAGE_DATE_FIN_ANTERIEURE = MESSAGE_DATE_FIN_APRES_DEBUT
MESSAGE_ANNEE_ARCHIVEE_MODIF = "Une année scolaire archivée est en lecture seule et ne peut pas être modifiée."


def construire_nom_annee(annee_courte):
    """
    Construit le nom canonique d'une année scolaire.
    Exemples :
        '26' -> ('2026-2027', 2026, 2027, None)
        '2026-2027' -> ('2026-2027', 2026, 2027, None)
        '27' -> ('2027-2028', 2027, 2028, None)
        '30' -> ('2030-2031', 2030, 2031, None)
        'abc', '2026', '2', None -> (None, None, None, MESSAGE_ANNEE_COURTE_INVALIDE)
    """
    if annee_courte is None:
        return None, None, None, MESSAGE_ANNEE_COURTE_INVALIDE

    val = str(annee_courte).strip()

    full_match = re.fullmatch(r'(20\d{2})\s*[-/]\s*(20\d{2})', val)
    if full_match:
        debut = int(full_match.group(1))
        fin = int(full_match.group(2))
        if fin != debut + 1:
            return None, None, None, "L'année de fin doit suivre l'année de début."
        return f"{debut}-{fin}", debut, fin, None

    if not re.fullmatch(r'\d{2}', val):
        return None, None, None, MESSAGE_ANNEE_COURTE_INVALIDE

    debut_suffixe = int(val)
    debut = 2000 + debut_suffixe
    fin = debut + 1
    nom = f"{debut}-{fin}"
    return nom, debut, fin, None


def valider_dates_annee(date_debut, date_fin, debut_annee: int, fin_annee: int):
    """
    Valide la cohérence des dates d'une année scolaire :
    - date_fin > date_debut
    - date_debut.year == debut_annee
    - date_fin.year == fin_annee
    """
    ok_intervalle, err_intervalle = valider_intervalle_dates(date_debut, date_fin)
    if not ok_intervalle:
        return False, err_intervalle

    if date_debut.year != debut_annee:
        return False, f"La date de début doit être dans l'année {debut_annee}."

    if date_fin.year != fin_annee:
        return False, f"La date de fin doit être dans l'année {fin_annee}."

    return True, None


def valider_unicite_annee(ecole_id: int, nom: str, annee_id_exclure: int = None):
    """
    Vérifie qu'aucune autre année scolaire n'a le même nom pour cette école.
    """
    query = AnneeScolaire.query.filter_by(ecole_id=ecole_id, nom=nom)
    if annee_id_exclure:
        query = query.filter(AnneeScolaire.id != annee_id_exclure)
    if query.first():
        return False, f"L'année scolaire {nom} existe déjà pour cet établissement."
    return True, None


SESSION_KEY = "annee_consultee"


def _ecole_key(ecole_id):
    return str(int(ecole_id)) if ecole_id is not None else None


def get_annee_active(ecole_id):
    if not ecole_id:
        return None
    return (
        AnneeScolaire.query
        .filter_by(ecole_id=ecole_id, statut="active")
        .order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc())
        .first()
    )


def get_annees_ecole(ecole_id):
    if not ecole_id:
        return []
    return (
        AnneeScolaire.query
        .filter_by(ecole_id=ecole_id)
        .order_by(AnneeScolaire.date_debut.desc(), AnneeScolaire.id.desc())
        .all()
    )


def _get_session_map():
    try:
        data = session.get(SESSION_KEY)
        return data if isinstance(data, dict) else {}
    except RuntimeError:
        return {}


def _get_session_annee_id(ecole_id):
    try:
        raw = session.get(SESSION_KEY)
    except RuntimeError:
        return None

    if isinstance(raw, dict):
        key = _ecole_key(ecole_id)
        if key and key in raw:
            try:
                return int(raw[key])
            except (TypeError, ValueError):
                return None
        return None

    if isinstance(raw, (int, str)):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return None


def set_annee_consultee(ecole_id, annee_id):
    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        return None

    data = dict(_get_session_map())
    data[_ecole_key(ecole_id)] = annee.id
    session[SESSION_KEY] = data
    session.modified = True
    try:
        if hasattr(g, '_annee_consultee_cache'):
            g._annee_consultee_cache.clear()
    except RuntimeError:
        pass
    return annee


def get_annee_consultee(ecole_id, annee_id=None):
    if not ecole_id:
        return None

    cache_key = (ecole_id, annee_id)
    try:
        if not hasattr(g, '_annee_consultee_cache'):
            g._annee_consultee_cache = {}
        if cache_key in g._annee_consultee_cache:
            return g._annee_consultee_cache[cache_key]
    except RuntimeError:
        pass

    annee = None
    if annee_id:
        annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    else:
        stored_id = _get_session_annee_id(ecole_id)
        if stored_id:
            annee = AnneeScolaire.query.filter_by(id=stored_id, ecole_id=ecole_id).first()
        if not annee:
            from app.utils import get_annee_active
            annee = get_annee_active(ecole_id)

    try:
        if hasattr(g, '_annee_consultee_cache'):
            g._annee_consultee_cache[cache_key] = annee
    except RuntimeError:
        pass

    return annee


def get_classes_annee(ecole_id, annee_id):
    return Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_id)


def get_eleves_annee_query(ecole_id, annee_id):
    return (
        Eleve.query
        .join(Inscription, Inscription.eleve_id == Eleve.id)
        .options(
            joinedload(Eleve.parent),
            joinedload(Eleve.classe),
        )
        .filter(
            Eleve.ecole_id == ecole_id,
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_id,
        )
    )


def get_inscriptions_annee_query(ecole_id, annee_id):
    return (
        Inscription.query
        .join(Eleve, Eleve.id == Inscription.eleve_id)
        .options(
            joinedload(Inscription.eleve).joinedload(Eleve.parent),
            joinedload(Inscription.classe),
        )
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_id,
            Eleve.ecole_id == ecole_id,
        )
    )


def get_niveaux_annee(ecole_id, annee_id):
    from app.services.structure_annuelle import get_niveaux_annee as _gna
    return _gna(ecole_id, annee_id)
