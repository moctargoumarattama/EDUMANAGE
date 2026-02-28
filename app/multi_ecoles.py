"""
Wrapper pour centraliser les fonctions multi-écoles.
Toutes les fonctions critiques proviennent de middleware.py
pour éviter la duplication et garantir la sécurité.
"""

from app.middleware import (
    get_ecole_courante,
    filtre_par_ecole,
    check_ecole_access as verifier_acces_ecole,
    ecole_access_required,
    get_ecole_id,
    set_ecole_courante,
    clear_ecole_courante,
    inject_ecole_courante
)