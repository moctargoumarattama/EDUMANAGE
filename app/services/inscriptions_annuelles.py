from datetime import datetime
from sqlalchemy import and_, or_

from app import db
from app.models import AnneeScolaire, Bulletin, Classe, Cours, Eleve, Inscription, Note, PeriodeBulletin
from app.services.classes_annuelles import classe_est_ouverte


STATUTS_INSCRIPTION = {"preinscrit", "inscrit", "termine", "sorti", "transfere", "diplome", "radie", "annulee"}
DECISIONS_FIN_ANNEE = {"passage", "redoublement", "transfert", "sortie", "fin_cycle", "diplome", "radiation"}


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


def classe_effective_pour_periode(inscription, periode):
    """Classe historique d'une période close, sinon classe active de l'inscription."""
    if not inscription or not periode:
        return inscription.classe_id if inscription else None
    bulletin = Bulletin.query.filter_by(
        ecole_id=inscription.ecole_id, annee_scolaire_id=inscription.annee_scolaire_id,
        inscription_id=inscription.id, periode=periode
    ).first()
    if bulletin is None:
        bulletin = Bulletin.query.filter_by(
            ecole_id=inscription.ecole_id, annee_scolaire_id=inscription.annee_scolaire_id,
            eleve_id=inscription.eleve_id, periode=periode
        ).first()
    # Un bulletin garde la classe dans laquelle il a été établi, même si la
    # période est rouverte par la suite.
    if bulletin and bulletin.classe_id:
        return bulletin.classe_id
    periode_publiee = PeriodeBulletin.query.filter_by(
        ecole_id=inscription.ecole_id, annee_id=inscription.annee_scolaire_id,
        nom=periode, publie=True
    ).first()
    if periode_publiee:
        classe_ids = {row[0] for row in db.session.query(Cours.classe_id).join(
            Note, Note.cours_id == Cours.id
        ).filter(
            or_(Note.inscription_id == inscription.id,
                and_(Note.inscription_id.is_(None), Note.eleve_id == inscription.eleve_id,
                     Note.annee_id == inscription.annee_scolaire_id)),
            Note.periode == periode, Note.ecole_id == inscription.ecole_id,
        ).distinct().all()}
        if len(classe_ids) == 1:
            return next(iter(classe_ids))
    return inscription.classe_id


def creer_inscription_annuelle(ecole_id, eleve_id, annee_scolaire_id, classe_id, statut=None, sync_active=True, frais_annuels=None, allow_archived=False):
    eleve, annee, classe, error = _validate_inscription_context(
        ecole_id, eleve_id, annee_scolaire_id, classe_id, allow_archived=allow_archived
    )
    if error:
        return None, error

    if statut is None:
        statut = "preinscrit" if annee.statut == "planifiee" else "inscrit"

    if statut not in STATUTS_INSCRIPTION:
        return None, "Statut d'inscription invalide."

    existing = Inscription.query.filter_by(
        ecole_id=ecole_id,
        eleve_id=eleve.id,
        annee_scolaire_id=annee.id,
    ).first()
    if existing:
        return None, "Une inscription existe deja pour cet eleve et cette annee scolaire."

    # Règle Phase 3B :
    # Si frais_annuels est fourni -> utiliser cette valeur (y compris 0.0)
    # Sinon -> snapshot de Eleve.frais_annuels (ou fallback 150000.0 si None)
    val_eleve = getattr(eleve, "frais_annuels", None)
    montant_frais = frais_annuels if frais_annuels is not None else (val_eleve if val_eleve is not None else 150000.0)

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
    if inscription.classe_id != classe.id:
        anciennes_notes = Note.query.join(Cours, Note.cours_id == Cours.id).filter(
            Note.ecole_id == ecole_id, Note.annee_id == annee.id,
            Note.eleve_id == eleve.id,
            or_(Note.inscription_id == inscription.id,
                and_(Note.inscription_id.is_(None), Cours.classe_id == inscription.classe_id)),
        ).all()
        periodes_publiees = {p.nom for p in PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id, annee_id=annee.id, publie=True
        ).all()}
        bulletins = Bulletin.query.filter_by(
            ecole_id=ecole_id, annee_scolaire_id=annee.id, eleve_id=eleve.id
        ).all()
        periodes_closes = periodes_publiees | {
            b.periode for b in bulletins if str(b.statut or '').casefold() in
            {'publie', 'publié', 'archive', 'archivé', 'archivee', 'archivée', 'verrouille', 'verrouillé'}
            or bool(getattr(b, 'verrouille', False))
        }
        if any(b.periode not in periodes_closes for b in bulletins):
            return None, "Transfert impossible : un bulletin provisoire doit être régénéré avant le changement de classe."
        cours_cibles = Cours.query.filter_by(ecole_id=ecole_id, classe_id=classe.id).all()
        par_nom = {}
        for cours in cours_cibles:
            par_nom.setdefault(cours.nom.strip().casefold(), []).append(cours)
        cours_inscription_cible = None
        if inscription.cours_id:
            cours_inscription = db.session.get(Cours, inscription.cours_id)
            if cours_inscription and cours_inscription.classe_id == inscription.classe_id:
                candidats = par_nom.get(cours_inscription.nom.strip().casefold(), [])
                if len(candidats) != 1:
                    return None, "Transfert impossible : le cours de l'inscription n'a pas d'équivalent unique dans la classe d'arrivée."
                cours_inscription_cible = candidats[0].id
        reaffectations = []
        for note in anciennes_notes:
            if note.periode in periodes_closes:
                continue
            ancien_cours = note.cours
            candidats = par_nom.get(ancien_cours.nom.strip().casefold(), []) if ancien_cours else []
            if len(candidats) != 1:
                return None, f"Transfert impossible : cours correspondant introuvable ou ambigu pour {ancien_cours.nom if ancien_cours else 'une note'}."
            doublon = Note.query.filter_by(
                ecole_id=ecole_id, eleve_id=eleve.id, cours_id=candidats[0].id,
                periode=note.periode, type_evaluation=note.type_evaluation,
                date_evaluation=note.date_evaluation,
            ).filter(Note.id != note.id).first()
            if doublon:
                return None, "Transfert impossible : une évaluation équivalente existe déjà dans la classe d'arrivée."
            reaffectations.append((note, candidats[0].id))
        for note, cours_cible_id in reaffectations:
            note.cours_id = cours_cible_id
            note.inscription_id = inscription.id
            note.sync_version = (note.sync_version or 1) + 1
            note.updated_at = datetime.utcnow()
        if cours_inscription_cible is not None:
            inscription.cours_id = cours_inscription_cible
    inscription.classe_id = classe.id
    if statut is not None:
        inscription.statut = statut
    if frais_annuels is not None:
        inscription.frais_annuels = frais_annuels
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


def transferer_ou_radier_eleve(
    ecole_id,
    eleve_id,
    statut="transfere",
    date_depart=None,
    etablissement_destination=None,
    motif_sortie=None,
    annee_scolaire_id=None,
    annee_source_id=None,
    decision=None,
):
    """
    Enregistre formellement le transfert ou la radiation d'un élève :
    1. Met à jour l'inscription en cours : statut = 'transfere' ou 'radie',
       avec enregistrement de date_depart et etablissement_destination.
    2. Recherche toute inscription ou préinscription future rattachée à cet élève
       (année non active ou date future) et l'annule automatiquement (statut = 'annulee')
       pour libérer la place dans les effectifs prévisionnels.
    3. Ne renvoie JAMAIS d'erreur d'idempotence « déjà traité » qui avorterait l'opération.
    4. Met à jour le statut de l'élève (eleve.statut).
    """
    if annee_source_id and not annee_scolaire_id:
        annee_scolaire_id = annee_source_id
    if decision:
        statut = decision

    eleve = Eleve.query.filter_by(id=eleve_id, ecole_id=ecole_id).first()
    if not eleve:
        return None, "Élève introuvable pour cet établissement."

    statut_normalise = "radie" if statut in ("radie", "radiation") else "transfere"
    decision = "radiation" if statut_normalise == "radie" else "transfert"

    inscription_en_cours = None
    if annee_scolaire_id:
        inscription_en_cours = Inscription.query.filter_by(
            ecole_id=ecole_id, eleve_id=eleve_id, annee_scolaire_id=annee_scolaire_id
        ).first()
        if not inscription_en_cours:
            return None, "Aucune inscription pour l'année scolaire demandée."

    if not inscription_en_cours:
        annee_active = AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut="active").first()
        if annee_active:
            inscription_en_cours = Inscription.query.filter_by(
                ecole_id=ecole_id, eleve_id=eleve_id, annee_scolaire_id=annee_active.id
            ).first()

    if not inscription_en_cours:
        inscription_en_cours = (
            Inscription.query.join(AnneeScolaire)
            .filter(
                Inscription.ecole_id == ecole_id,
                Inscription.eleve_id == eleve_id,
                AnneeScolaire.statut != "archivee"
            )
            .order_by(AnneeScolaire.date_debut.desc())
            .first()
        )

    if not inscription_en_cours:
        return None, "Aucune inscription active ou en cours trouvée pour cet élève."

    if not inscription_en_cours.annee_scolaire or inscription_en_cours.annee_scolaire.statut != "active":
        return None, "Un transfert ou une radiation exige une année scolaire active."
    if inscription_en_cours.statut not in ("inscrit", "preinscrit", "actif"):
        return None, "L'inscription ne permet plus une décision de transfert ou radiation."

    if isinstance(date_depart, str):
        try:
            date_depart_val = datetime.strptime(date_depart, "%Y-%m-%d").date()
        except ValueError:
            date_depart_val = datetime.utcnow().date()
    elif hasattr(date_depart, "strftime") and hasattr(date_depart, "year"):
        date_depart_val = date_depart if hasattr(date_depart, "month") else datetime.utcnow().date()
    else:
        date_depart_val = datetime.utcnow().date()

    # 1. Mise à jour de l'inscription en cours
    inscription_en_cours.statut = statut_normalise
    inscription_en_cours.decision_fin_annee = decision
    inscription_en_cours.motif_sortie = motif_sortie or f"{decision.capitalize()} de l'établissement"
    inscription_en_cours.date_sortie = datetime.utcnow()
    inscription_en_cours.date_depart = date_depart_val
    if etablissement_destination:
        inscription_en_cours.etablissement_destination = etablissement_destination
    inscription_en_cours.updated_at = datetime.utcnow()

    # 2. Mise à jour de l'élève
    eleve.statut = statut_normalise
    eleve.updated_at = datetime.utcnow()

    # 3. Rechercher et annuler toute préinscription ou inscription future
    annee_source = inscription_en_cours.annee_scolaire
    inscriptions_futures = (
        Inscription.query.join(AnneeScolaire)
        .filter(
            Inscription.ecole_id == ecole_id,
            Inscription.eleve_id == eleve_id,
            Inscription.id != inscription_en_cours.id,
            or_(
                AnneeScolaire.statut == "planifiee",
                AnneeScolaire.date_debut > annee_source.date_debut,
            ),
            Inscription.statut.in_(("preinscrit", "inscrit")),
        )
        .all()
    )

    for fut_insc in inscriptions_futures:
        fut_insc.statut = "annulee"
        fut_insc.motif_sortie = f"Préinscription annulée suite au {decision} du {date_depart_val}"
        fut_insc.date_sortie = datetime.utcnow()
        fut_insc.updated_at = datetime.utcnow()

    db.session.flush()
    return inscription_en_cours, None
