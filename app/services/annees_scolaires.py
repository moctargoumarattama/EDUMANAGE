from flask import session
from sqlalchemy.orm import joinedload

from app.models import AnneeScolaire, Classe, Eleve, Inscription


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
    return annee


def get_annee_consultee(ecole_id, annee_id=None):
    if not ecole_id:
        return None

    if annee_id:
        return AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()

    stored_id = _get_session_annee_id(ecole_id)
    if stored_id:
        annee = AnneeScolaire.query.filter_by(id=stored_id, ecole_id=ecole_id).first()
        if annee:
            return annee

    return get_annee_active(ecole_id)


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
