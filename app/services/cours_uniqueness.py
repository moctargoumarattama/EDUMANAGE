from sqlalchemy import func

from app.models import Cours


def normalize_cours_nom(nom):
    return " ".join((nom or "").split())


def find_duplicate_cours(ecole_id, classe_id, nom, exclude_id=None):
    normalized = normalize_cours_nom(nom)
    if not normalized:
        return None

    query = Cours.query.filter(
        Cours.ecole_id == ecole_id,
        Cours.classe_id == classe_id,
        func.lower(func.trim(Cours.nom)) == normalized.lower(),
    )
    if exclude_id is not None:
        query = query.filter(Cours.id != exclude_id)
    return query.first()
