"""Matricules scolaires permanents, indépendants des accès parent.

La réservation appartient à la transaction qui crée l'élève : un rollback
annule aussi la réservation. Le verrou établissement sérialise les créations
sur PostgreSQL et SQLite, y compris les contrôles d'identité avant insertion.
"""
import re
from datetime import datetime

import sqlalchemy as sa

from app.extensions import db


MATRICULE_PATTERN = re.compile(r"^[0-9]{2}-[0-9]{4}$")
MAX_ELEVES_PAR_ANNEE = 9999


def matricule_conforme(value):
    return bool(
        isinstance(value, str)
        and MATRICULE_PATTERN.fullmatch(value)
        and value[-4:] != "0000"
    )


def verrouiller_ecole(ecole_id, connection=None):
    """Garde le verrou jusqu'au commit/rollback, sans modifier l'établissement."""
    from app.models import Ecole

    connection = connection if connection is not None else db.session.connection()
    table = Ecole.__table__
    if connection.dialect.name == "sqlite":
        # SQLite ne possède pas SELECT FOR UPDATE : prendre son verrou d'écriture.
        result = connection.execute(
            table.update().where(table.c.id == ecole_id).values(id=table.c.id)
        )
        exists = result.rowcount != 0
    else:
        exists = connection.execute(
            sa.select(table.c.id).where(table.c.id == ecole_id).with_for_update()
        ).scalar_one_or_none() is not None
    if not exists:
        raise ValueError("Établissement introuvable pour attribuer un matricule.")


def _reserver_matricule(connection, ecole_id, annee=None):
    from app.models import Eleve, MatriculeSequence

    annee = datetime.now().year if annee is None else int(annee)
    if not 1000 <= annee <= 9999:
        raise ValueError("L'année d'entrée doit comporter quatre chiffres.")
    prefixe = f"{annee % 100:02d}"
    verrouiller_ecole(ecole_id, connection)

    eleves = Eleve.__table__
    maximum_existant = connection.execute(
        sa.select(eleves.c.matricule).where(
            eleves.c.ecole_id == ecole_id,
            eleves.c.matricule.like(f"{prefixe}-%"),
        ).order_by(eleves.c.matricule.desc()).limit(1)
    ).scalar_one_or_none()
    if maximum_existant and not matricule_conforme(maximum_existant):
        raise ValueError("Migrez les anciens matricules avant de créer de nouveaux élèves.")
    maximum = int(maximum_existant[-4:]) if maximum_existant else 0
    sequences = MatriculeSequence.__table__
    condition = sa.and_(
        sequences.c.ecole_id == ecole_id, sequences.c.prefixe == prefixe
    )
    reserve = connection.execute(
        sa.select(sequences.c.dernier_numero).where(condition)
    ).scalar_one_or_none()
    prochain = max(maximum, reserve or 0) + 1
    if prochain > MAX_ELEVES_PAR_ANNEE:
        raise ValueError(
            f"La limite de 9 999 nouveaux élèves pour {annee} dans cet établissement est atteinte."
        )
    if reserve is None:
        connection.execute(sequences.insert().values(
            ecole_id=ecole_id, prefixe=prefixe, dernier_numero=prochain
        ))
    else:
        connection.execute(sequences.update().where(condition).values(dernier_numero=prochain))
    return f"{prefixe}-{prochain:04d}"


def generer_prochain_matricule(ecole_id, annee=None) -> str:
    """Réserve AA-XXXX pour l'école dans la transaction courante, sans commit."""
    with db.session.no_autoflush:
        return _reserver_matricule(db.session.connection(), ecole_id, annee)


def attribuer_matricule(mapper, connection, eleve):
    """Filet de sécurité commun à toutes les créations ORM, imports compris."""
    if eleve.matricule:
        if not matricule_conforme(eleve.matricule):
            raise ValueError("Le matricule scolaire doit respecter le format AA-XXXX (0001 à 9999).")
        return
    date_entree = eleve.date_inscription or datetime.now()
    annee = eleve.annee_premiere_ecole or date_entree.year
    eleve.matricule = _reserver_matricule(connection, eleve.ecole_id, annee)


def proteger_matricule(mapper, connection, eleve):
    history = sa.inspect(eleve).attrs.matricule.history
    if history.has_changes():
        # L'ancienne valeur peut être expirée après commit : relire aussi en base.
        table = eleve.__table__
        ancien = connection.execute(
            sa.select(table.c.matricule).where(table.c.id == eleve.id)
        ).scalar_one_or_none()
        if ancien and ancien != eleve.matricule:
            raise ValueError("Le matricule scolaire est permanent et ne peut pas être modifié.")
    if not eleve.matricule:
        attribuer_matricule(mapper, connection, eleve)
    elif not matricule_conforme(eleve.matricule):
        raise ValueError("Le matricule scolaire doit respecter le format AA-XXXX.")


def installer_protection_matricule(table, connection, **kwargs):
    # Le même garde-fou SQL protège migrations et installations db.create_all().
    from scripts.migrate_matricules_format import _proteger_permanence

    _proteger_permanence(connection)
