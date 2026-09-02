from . import main
from .common import (
    render_template,
)


@main.errorhandler(404)
def page_not_found(e):
    """Gestionnaire d'erreur 404 - Page non trouvée"""
    return render_template('404.html'), 404
@main.errorhandler(403)
def access_denied(e):
    """Gestionnaire d'erreur 403 - Accès refusé"""
    return render_template('403.html'), 403
@main.errorhandler(500)
def server_error(e):
    """Gestionnaire d'erreur 500 - Erreur serveur interne"""
    return render_template('500.html'), 500
