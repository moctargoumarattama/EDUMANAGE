"""
Utilitaires centralisés pour le tri pédagogique des classes.

L'ordre pédagogique suit la progression scolaire naturelle :
CI → CP → CE1 → CE2 → CM1 → CM2 → 6e → 5e → 4e → 3e → 2nde → 1ere → Terminale

Cet ordre est encodé dans la colonne NiveauScolaire.ordre (1 à 13).
"""
from app.models import Classe, NiveauScolaire, db


def ordre_pedagogique_classe():
    """Retourne les clauses ORDER BY pour trier les classes par ordre pédagogique.

    Trie d'abord par NiveauScolaire.ordre (CI=1 → Terminale=13),
    puis par Classe.nom (pour différencier 6e A, 6e B, 6e C au sein du même niveau).

    Usage::

        query.outerjoin(NiveauScolaire, Classe.niveau_id == NiveauScolaire.id)\\
             .order_by(*ordre_pedagogique_classe())
    """
    return (NiveauScolaire.ordre.asc(), Classe.nom.asc())


def classes_triees_pedagogique(query):
    """Applique le tri pédagogique à un query de classes.

    Fait automatiquement l'outerjoin sur NiveauScolaire si nécessaire,
    puis trie par NiveauScolaire.ordre, Classe.nom.

    Args:
        query: Un SQLAlchemy query portant sur Classe.

    Returns:
        Le même query avec le tri pédagogique appliqué.
    """
    return (
        query
        .outerjoin(NiveauScolaire, Classe.niveau_id == NiveauScolaire.id)
        .order_by(NiveauScolaire.ordre.asc(), Classe.nom.asc())
    )

