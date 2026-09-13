"""
Service d'accès et de filtrage des élèves — Phase 5F.

Fournit `rechercher_eleves()` : point d'entrée unifié pour la recherche
et le filtrage des élèves avec isolation stricte (ecole_id + annee_id).
"""
from __future__ import annotations

from app import db
from app.models import Classe, Eleve, Inscription


def rechercher_eleves(
    ecole_id: int,
    annee_id: int,
    search: str = "",
    classe_id: int | None = None,
    niveau: str = "",
    genre: str = "",
    statut: str = "",
    page: int = 1,
    par_page: int = 0,
) -> list:
    """
    Retourne la liste des élèves filtrés pour une école et une année scolaire données.

    Paramètres :
        ecole_id   : identifiant de l'école courante (obligatoire).
        annee_id   : identifiant de l'année scolaire consultée (obligatoire).
        search     : recherche textuelle sur nom, prénom, matricule ou code_parent.
        classe_id  : filtre par classe (l'appartenance à l'école est vérifiée).
        niveau     : filtre par niveau scolaire (chaîne, ex: "6ème").
        genre      : filtre par genre ("M" / "F").
        statut     : filtre par statut de l'élève ("actif", "inactif"…).
        page       : numéro de page (1-indexé), ignoré si par_page == 0.
        par_page   : taille de page ; 0 = pas de pagination (tout retourner).

    Retourne :
        Une liste d'objets `Eleve`.
    """
    # Base : élèves inscrits dans l'école et l'année données
    query = (
        db.session.query(Eleve)
        .join(Inscription, Inscription.eleve_id == Eleve.id)
        .join(Classe, Classe.id == Inscription.classe_id)
        .filter(
            Eleve.ecole_id == ecole_id,
            Inscription.annee_scolaire_id == annee_id,
            Classe.ecole_id == ecole_id,          # isolation : la classe doit appartenir à la même école
        )
        .distinct()
    )

    # Recherche textuelle
    if search:
        pat = f"%{search}%"
        query = query.filter(
            db.or_(
                Eleve.nom.ilike(pat),
                Eleve.prenom.ilike(pat),
                Eleve.code_parent.ilike(pat),
                Eleve.contact_parent.ilike(pat),
            )
        )

    # Filtre par classe — vérifie que la classe appartient à la même école
    if classe_id:
        classe_valide = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
        if not classe_valide:
            return []  # classe inconnue ou d'une autre école → liste vide
        query = query.filter(Inscription.classe_id == classe_id)

    # Filtre par niveau
    if niveau:
        query = query.filter(Classe.niveau == niveau)

    # Filtre par genre
    if genre:
        query = query.filter(Eleve.genre == genre)

    # Filtre par statut de l'élève
    if statut:
        query = query.filter(Eleve.statut == statut)

    query = query.order_by(Eleve.nom.asc(), Eleve.prenom.asc())

    if par_page and par_page > 0:
        offset = (page - 1) * par_page
        return query.offset(offset).limit(par_page).all()

    return query.all()
