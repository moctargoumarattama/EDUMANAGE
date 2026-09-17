from datetime import datetime

from app import db
from app.models import AnneeScolaire, Classe, Eleve, Inscription
from app.services.classes_annuelles import classe_est_ouverte


STATUTS_INSCRIPTION = {"inscrit", "termine", "sorti", "transfere", "diplome"}
DECISIONS_FIN_ANNEE = {"passage", "redoublement", "transfert", "sortie", "fin_cycle", "diplome"}


def get_inscription(eleve, annee):
    if not eleve or not annee:
        return None
    return Inscription.query.filter_by(
        ecole_id=eleve.ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
    ).first()


def get_inscription_active(eleve):
    if not eleve:
        return None
    annee = AnneeScolaire.query.filter_by(ecole_id=eleve.ecole_id, statut="active").first()
    return get_inscription(eleve, annee)


def get_parcours_eleve(eleve):
    if not eleve:
        return []
    return (
        Inscription.query
        .join(AnneeScolaire, AnneeScolaire.id == Inscription.annee_scolaire_id)
        .filter(Inscription.ecole_id == eleve.ecole_id, Inscription.eleve_id == eleve.id)
        .order_by(AnneeScolaire.date_debut.asc(), Inscription.id.asc())
        .all()
    )


def _validate_inscription_context(ecole_id, eleve_id, annee_scolaire_id, classe_id, allow_archived=False):
    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        return None, None, None, "Eleve introuvable pour cet etablissement."

    annee = AnneeScolaire.query.filter_by(id=annee_scolaire_id, ecole_id=ecole_id).first()
    if not annee:
        return None, None, None, "Annee scolaire invalide pour cet etablissement."
    if annee.statut == "archivee" and not allow_archived:
        return None, None, None, "Impossible de modifier une inscription d'une annee archivee."

    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return None, None, None, "Classe invalide pour cet etablissement."
    if classe.annee_scolaire_id != annee.id:
        return None, None, None, "La classe n'appartient pas a cette annee scolaire."
    if not classe_est_ouverte(classe):
        return None, None, None, "La classe est fermee pour cette annee scolaire."

    return eleve, annee, classe, None


def _sync_classe_active(eleve, annee, classe):
    pass


def creer_inscription_annuelle(ecole_id, eleve_id, annee_scolaire_id, classe_id, statut="inscrit", sync_active=True, frais_annuels=None, allow_archived=False):
    if statut not in STATUTS_INSCRIPTION:
        return None, "Statut d'inscription invalide."

    eleve, annee, classe, error = _validate_inscription_context(
        ecole_id, eleve_id, annee_scolaire_id, classe_id, allow_archived=allow_archived
    )
    if error:
        return None, error

    existing = Inscription.query.filter_by(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
    ).first()
    if existing:
        return None, "Une inscription existe deja pour cet eleve et cette annee scolaire."

    # Règle Phase 3B :
    # Si frais_annuels est fourni -> utiliser cette valeur
    # Sinon -> snapshot de Eleve.frais_annuels (ou fallback 150000.0)
    montant_frais = frais_annuels if frais_annuels is not None else getattr(eleve, "frais_annuels", 150000.0)

    inscription = Inscription(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
        classe_id=classe.id,
        cours_id=None,
        statut=statut,
        frais_annuels=montant_frais,
        date_inscription=datetime.utcnow(),
    )
    db.session.add(inscription)
    if sync_active:
        _sync_classe_active(eleve, annee, classe)
    db.session.flush()
    return inscription, None


def modifier_inscription_annuelle(ecole_id, eleve_id, annee_scolaire_id, classe_id, statut=None, sync_active=True, frais_annuels=None):
    eleve, annee, classe, error = _validate_inscription_context(ecole_id, eleve_id, annee_scolaire_id, classe_id)
    if error:
        return None, error

    inscription = Inscription.query.filter_by(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
    ).first()
    if not inscription:
        return creer_inscription_annuelle(
            ecole_id=ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=annee.id,
            classe_id=classe.id,
            statut=statut or "inscrit",
            sync_active=sync_active,
            frais_annuels=frais_annuels,
        )

    if statut is not None:
        if statut not in STATUTS_INSCRIPTION:
            return None, "Statut d'inscription invalide."
        inscription.statut = statut
    if frais_annuels is not None:
        inscription.frais_annuels = frais_annuels
    inscription.classe_id = classe.id
    inscription.updated_at = datetime.utcnow()
    if sync_active:
        _sync_classe_active(eleve, annee, classe)
    db.session.flush()
    return inscription, None


def terminer_inscription(inscription, decision_fin_annee=None, statut="termine", motif_sortie=None):
    if not inscription:
        return None, "Inscription introuvable."
    if inscription.annee_scolaire and inscription.annee_scolaire.statut == "archivee":
        return None, "Impossible de modifier une inscription d'une annee archivee."
    if statut not in STATUTS_INSCRIPTION:
        return None, "Statut d'inscription invalide."
    if decision_fin_annee and decision_fin_annee not in DECISIONS_FIN_ANNEE:
        return None, "Decision de fin d'annee invalide."

    inscription.statut = statut
    inscription.decision_fin_annee = decision_fin_annee
    inscription.motif_sortie = motif_sortie
    inscription.date_sortie = datetime.utcnow()
    inscription.updated_at = datetime.utcnow()
    db.session.flush()
    return inscription, None
