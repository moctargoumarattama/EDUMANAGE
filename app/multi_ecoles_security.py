from flask import flash, redirect, url_for, render_template, abort
from functools import wraps
from app.middleware import get_ecole_courante, check_ecole_access


def ecole_access_required(obj_class, param_id_name):
    """Décorateur pour vérifier que l'objet appartient à l'école courante."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            obj_id = kwargs.get(param_id_name)
            
            # Vérifier l'accès AVANT de charger l'objet
            if not check_ecole_access(obj_class, obj_id):
                abort(403, "Accès non autorisé à cette ressource.")
            
            # Si on arrive ici, l'accès est autorisé
            return f(*args, **kwargs)
        return wrapped
    return decorator