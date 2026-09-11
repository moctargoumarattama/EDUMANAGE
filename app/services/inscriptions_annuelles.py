from datetime import datetime

from app import db
from app.models import AnneeScolaire, Classe, Eleve, Inscription


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

    return eleve, annee, classe, None


def _sync_classe_active(eleve, annee, classe):
    if annee.statut == "active":
        eleve.classe_id = classe.id


def creer_inscription_annuelle(ecole_id, eleve_id, annee_scolaire_id, classe_id, statut="inscrit", sync_active=True):
    if statut not in STATUTS_INSCRIPTION:
        return None, "Statut d'inscription invalide."

    eleve, annee, classe, error = _validate_inscription_context(ecole_id, eleve_id, annee_scolaire_id, classe_id)
    if error:
        return None, error

    existing = Inscription.query.filter_by(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
    ).first()
    if existing:
        return None, "Une inscription existe deja pour cet eleve et cette annee scolaire."

    inscription = Inscription(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
        classe_id=classe.id,
        cours_id=None,
        statut=statut,
        date_inscription=datetime.utcnow(),
    )
    db.session.add(inscription)
    if sync_active:
        _sync_classe_active(eleve, annee, classe)
    db.session.flush()
    return inscription, None


def modifier_inscription_annuelle(ecole_id, eleve_id, annee_scolaire_id, classe_id, statut=None, sync_active=True):
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
        )

    if statut is not None:
        if statut not in STATUTS_INSCRIPTION:
            return None, "Statut d'inscription invalide."
        inscription.statut = statut
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


def auditer_backfill_inscriptions():
    stats = {
        "total_eleves": 0,
        "avec_classe_id": 0,
        "sans_classe_id": 0,
        "classe_coherente": 0,
        "classe_incoherente": 0,
        "inscription_existante": 0,
        "a_creer": 0,
        "impossible": 0,
    }
    anomalies = []
    for eleve in Eleve.query.all():
        stats["total_eleves"] += 1
        if not eleve.classe_id:
            stats["sans_classe_id"] += 1
            continue
        stats["avec_classe_id"] += 1
        classe = db.session.get(Classe, eleve.classe_id)
        if not classe or classe.ecole_id != eleve.ecole_id or not classe.annee_scolaire_id:
            stats["classe_incoherente"] += 1
            stats["impossible"] += 1
            anomalies.append({"eleve_id": eleve.id, "classe_id": eleve.classe_id})
            continue
        stats["classe_coherente"] += 1
        existing = Inscription.query.filter_by(
            ecole_id=eleve.ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=classe.annee_scolaire_id,
        ).first()
        if existing:
            stats["inscription_existante"] += 1
        else:
            stats["a_creer"] += 1
    return stats, anomalies


def backfill_inscriptions_depuis_classe_courante(commit=False):
    created = 0
    anomalies = []
    for eleve in Eleve.query.all():
        if not eleve.classe_id:
            continue
        classe = db.session.get(Classe, eleve.classe_id)
        if not classe or classe.ecole_id != eleve.ecole_id or not classe.annee_scolaire_id:
            anomalies.append({"eleve_id": eleve.id, "classe_id": eleve.classe_id})
            continue
        existing = Inscription.query.filter_by(
            ecole_id=eleve.ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=classe.annee_scolaire_id,
        ).first()
        if existing:
            continue
        inscription, error = creer_inscription_annuelle(
            ecole_id=eleve.ecole_id,
            eleve_id=eleve.id,
            annee_scolaire_id=classe.annee_scolaire_id,
            classe_id=classe.id,
            sync_active=False,
        )
        if error:
            anomalies.append({"eleve_id": eleve.id, "classe_id": classe.id, "error": error})
        else:
            created += 1
    if commit:
        db.session.commit()
    return created, anomalies
