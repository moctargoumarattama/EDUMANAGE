from flask import Blueprint

# --- Création du blueprint admin ---
admin_bp = Blueprint(
    'admin',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/admin/static'
)

# --- Import des routes après la création du blueprint ---
from . import routes
