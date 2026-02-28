from app.middleware import get_ecole_courante


def ajouter_ecole_id(obj):
    """Assigne automatiquement l'ecole_id à un objet avant création."""
    ecole = get_ecole_courante()
    if ecole and not isinstance(ecole, tuple) and hasattr(obj, 'ecole_id'):
        obj.ecole_id = ecole.id