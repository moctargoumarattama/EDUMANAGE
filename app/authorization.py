from flask import flash, redirect, url_for, current_app, request, jsonify, g
from flask_login import current_user
from functools import wraps
from app.models import Eleve
from app.middleware import get_ecole_courante, log_action


# -----------------------
# Vérification parent/élève
# -----------------------
def check_parent_access(eleve_id):
    """Compatible relation simple (parent_id) et multiple (parents)."""
    if current_user.role != 'parent':
        return True

    eleve = Eleve.query.get(eleve_id)
    if not eleve:
        return False

    # Cas 1 : relation simple
    if hasattr(eleve, "parent_id") and eleve.parent_id:
        return eleve.parent_id == current_user.id

    # Cas 2 : relation multiple (si jamais ajoutée plus tard)
    if hasattr(eleve, "parents"):
        return any(p.id == current_user.id for p in eleve.parents)

    return False


def parent_access_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        eleve_id = kwargs.get('eleve_id')
        if not eleve_id or not check_parent_access(eleve_id):
            flash("Accès refusé : cet élève ne vous appartient pas.", "danger")
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

# -----------------------
# Décorateur rôle avec compatibilité anciens rôles
# -----------------------
ROLE_ALIAS = {
    "enseignant": ["enseignant", "professeur", "prof", "teacher"],
    "admin": ["admin", "administrateur"],
    "super_admin": ["super_admin", "super-admin", "superadmin"],
    "parent": ["parent"]
}

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                message = "Vous devez être connecté pour accéder à cette ressource."
                if request.is_json:
                    return jsonify({"error": message}), 401
                flash(message, "warning")
                return redirect(url_for('main.login'))

            if current_user.role == 'super_admin':
                return f(*args, **kwargs)

            # Vérification parents
            if 'parent' in roles and current_user.role == 'parent':
                eleve_id = kwargs.get('eleve_id')
                if eleve_id and not check_parent_access(eleve_id):
                    message = "Accès refusé : cet élève ne vous appartient pas."
                    if request.is_json:
                        return jsonify({"error": message}), 403
                    flash(message, "danger")
                    return redirect(url_for('main.parent_dashboard'))

            # Vérification rôle avec alias
            user_role = current_user.role
            allowed_roles = []
            for r in roles:
                allowed_roles.extend(ROLE_ALIAS.get(r, [r]))

            if user_role not in allowed_roles:
                message = f"Accès non autorisé pour le rôle '{user_role}'."
                log_action(
                    module="authorization",
                    action="Accès refusé",
                    level="WARNING",
                    details=f"Tentative d'accès {request.path} par {user_role}"
                )
                if request.is_json:
                    return jsonify({"error": message}), 403
                flash(message, "danger")
                return redirect(url_for('main.index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

# -----------------------
# Décorateur pour injecter l'année active
# -----------------------
def with_annee_active(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ecole = get_ecole_courante()
        if isinstance(ecole, tuple) or not ecole:
            flash("Veuillez choisir une école pour continuer.", "warning")
            return redirect(url_for('main.choisir_ecole'))

        # Import local pour éviter les dépendances circulaires
        from app.utils import get_annee_active
        annee_active = get_annee_active(ecole.id)
        if not annee_active:
            flash("Aucune année scolaire active n'est configurée pour cette école.", "warning")
            return redirect(url_for('main.configurer_annees'))

        g.annee_active = annee_active
        return f(*args, **kwargs)
    return decorated_function