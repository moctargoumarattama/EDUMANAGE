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
    if getattr(current_user, "ecole_id", None) and getattr(eleve, "ecole_id", None) != current_user.ecole_id:
        return False

    # Cas 1 : relation simple
    if hasattr(eleve, "parent_id") and eleve.parent_id:
        return eleve.parent_id == current_user.id

    # Cas 2 : relation multiple (si jamais ajoutée plus tard)
    if hasattr(eleve, "parents"):
        return any(p.id == current_user.id for p in eleve.parents)

    return False


def get_current_professeur():
    if getattr(current_user, "role", None) not in ("professeur", "enseignant"):
        return None
    return getattr(current_user, "professeur_rel", None)


def can_access_class(classe):
    if not classe or not getattr(current_user, "is_authenticated", False):
        return False
    role = getattr(current_user, "role", None)
    if role == "admin":
        return classe.ecole_id == current_user.ecole_id
    if role in ("professeur", "enseignant"):
        professeur = get_current_professeur()
        if not professeur or classe.ecole_id != current_user.ecole_id:
            return False
        if getattr(classe, "professeur_id", None) == professeur.id:
            return True
        from app import db
        from app.models import professeur_classes
        return db.session.query(professeur_classes).filter(
            professeur_classes.c.professeur_id == professeur.id,
            professeur_classes.c.classe_id == classe.id
        ).first() is not None
    return False


def can_access_eleve(eleve):
    if not eleve or not getattr(current_user, "is_authenticated", False):
        return False
    role = getattr(current_user, "role", None)
    if role == "admin":
        return eleve.ecole_id == current_user.ecole_id
    if role == "parent":
        return check_parent_access(eleve.id)
    if role in ("professeur", "enseignant"):
        return eleve.ecole_id == current_user.ecole_id and can_access_class(eleve.classe)
    return False


def can_access_cours(cours):
    if not cours or not getattr(current_user, "is_authenticated", False):
        return False
    role = getattr(current_user, "role", None)
    if role == "admin":
        return cours.ecole_id == current_user.ecole_id
    if role in ("professeur", "enseignant"):
        professeur = get_current_professeur()
        if not professeur or cours.ecole_id != current_user.ecole_id:
            return False
        return cours.professeur_id == professeur.id or can_access_class(cours.classe)
    return False


def can_access_note(note):
    if not note:
        return False
    role = getattr(current_user, "role", None)
    if role == "parent":
        return note.eleve is not None and can_access_eleve(note.eleve)
    if getattr(note, "ecole_id", None) and role == "admin":
        return note.ecole_id == current_user.ecole_id
    return can_access_eleve(note.eleve) and can_access_cours(note.cours)


def can_access_absence(absence):
    if not absence:
        return False
    role = getattr(current_user, "role", None)
    if role == "parent":
        return absence.eleve is not None and can_access_eleve(absence.eleve)
    if getattr(absence, "ecole_id", None) and role == "admin":
        return absence.ecole_id == current_user.ecole_id
    return can_access_eleve(absence.eleve) and (absence.cours is None or can_access_cours(absence.cours))


def can_access_paiement(paiement):
    if not paiement:
        return False
    if getattr(current_user, "role", None) == "admin":
        return paiement.ecole_id == current_user.ecole_id
    if getattr(current_user, "role", None) == "parent":
        return paiement.eleve is not None and can_access_eleve(paiement.eleve)
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
    "enseignant": ["enseignant", "professeur"],
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
            return redirect(url_for('main.gestion_annees'))

        g.annee_active = annee_active
        return f(*args, **kwargs)
    return decorated_function
