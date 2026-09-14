from datetime import timedelta

from sqlalchemy.orm import joinedload

from app import db
from app.models import Absence, AnneeScolaire, Bulletin, Inscription, PeriodeBulletin, Presence


SEMESTRE_1 = "Semestre 1"
SEMESTRE_2 = "Semestre 2"
SEMESTRES = (SEMESTRE_1, SEMESTRE_2)
MESSAGE_CALENDRIER_ABSENT = "Calendrier des semestres non configuré"
MESSAGE_ARCHIVE_LECTURE_SEULE = "Année archivée : calendrier des semestres en lecture seule."


def _periode_query(ecole_id, annee_id):
    return PeriodeBulletin.query.filter(
        PeriodeBulletin.ecole_id == ecole_id,
        PeriodeBulletin.annee_id == annee_id,
        PeriodeBulletin.nom.in_(SEMESTRES),
    )


def get_semestres_annee(ecole_id, annee_id):
    if not ecole_id or not annee_id:
        return []
    periodes = _periode_query(ecole_id, annee_id).order_by(PeriodeBulletin.nom.asc()).all()
    return sorted(periodes, key=lambda p: 0 if p.nom == SEMESTRE_1 else 1)


def get_periode_semestre(ecole_id, annee_id, semestre):
    if semestre not in SEMESTRES:
        return None
    return _periode_query(ecole_id, annee_id).filter(PeriodeBulletin.nom == semestre).first()


def calendrier_configure(ecole_id, annee_id):
    periodes = get_semestres_annee(ecole_id, annee_id)
    return len(periodes) == 2 and all(p.date_debut and p.date_fin for p in periodes)


def calculer_bornes_semestres(annee, fin_semestre_1):
    if not annee:
        return None, "Année scolaire introuvable."
    if not fin_semestre_1:
        return None, "La fin du Semestre 1 est obligatoire."
    if not annee.date_debut or not annee.date_fin:
        return None, "Les dates de l'année scolaire sont obligatoires."
    if not (annee.date_debut < fin_semestre_1 < annee.date_fin):
        return None, "La fin du Semestre 1 doit être comprise entre le début et la fin de l'année."

    debut_semestre_2 = fin_semestre_1 + timedelta(days=1)
    return {
        SEMESTRE_1: (annee.date_debut, fin_semestre_1),
        SEMESTRE_2: (debut_semestre_2, annee.date_fin),
    }, None


def configurer_semestres_annee(ecole_id, annee_id, fin_semestre_1, *, force=False):
    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        return None, "Année scolaire introuvable ou non autorisée."
    if annee.statut == "archivee":
        return None, MESSAGE_ARCHIVE_LECTURE_SEULE

    bornes, err = calculer_bornes_semestres(annee, fin_semestre_1)
    if err:
        return None, err

    periodes_existantes = {p.nom: p for p in get_semestres_annee(ecole_id, annee_id)}
    deja_configure = any(p.date_debut or p.date_fin for p in periodes_existantes.values())
    publiees = [p for p in periodes_existantes.values() if p.publie]
    if annee.statut == "active" and deja_configure and publiees and not force:
        return None, "Modification refusée : un bulletin utilisant cette période est déjà publié."

    periodes = []
    for nom in SEMESTRES:
        periode = periodes_existantes.get(nom)
        if not periode:
            periode = PeriodeBulletin(nom=nom, annee_id=annee.id, ecole_id=ecole_id, publie=False, periode_active=False)
            db.session.add(periode)
        periode.date_debut, periode.date_fin = bornes[nom]
        periodes.append(periode)

    db.session.commit()
    return periodes, None


def date_dans_semestre(date_value, periode):
    if not date_value or not periode or not periode.date_debut or not periode.date_fin:
        return False
    return periode.date_debut <= date_value <= periode.date_fin


def _inscription_autorisee(ecole_id, annee_id, inscription_id, eleve_id=None):
    query = Inscription.query.filter(
        Inscription.ecole_id == ecole_id,
        Inscription.annee_scolaire_id == annee_id,
    )
    if inscription_id:
        query = query.filter(Inscription.id == inscription_id)
    if eleve_id:
        query = query.filter(Inscription.eleve_id == eleve_id)
    return query.first()


def compter_absences_semestre(ecole_id, annee_id, inscription_id, semestre):
    periode = get_periode_semestre(ecole_id, annee_id, semestre)
    if not periode or not periode.date_debut or not periode.date_fin:
        return None
    inscription = _inscription_autorisee(ecole_id, annee_id, inscription_id)
    if not inscription:
        return 0
    return Absence.query.filter(
        Absence.ecole_id == ecole_id,
        Absence.inscription_id == inscription.id,
        Absence.date_absence >= periode.date_debut,
        Absence.date_absence <= periode.date_fin,
    ).count()


def compter_retards_semestre(ecole_id, annee_id, inscription_id, semestre):
    periode = get_periode_semestre(ecole_id, annee_id, semestre)
    if not periode or not periode.date_debut or not periode.date_fin:
        return None
    return 0


def bulletin_publie_pour_periode(ecole_id, annee_id, periode_nom):
    return Bulletin.query.options(joinedload(Bulletin.inscription)).filter(
        Bulletin.ecole_id == ecole_id,
        Bulletin.annee_scolaire_id == annee_id,
        Bulletin.periode == periode_nom,
        Bulletin.statut == "valide",
    ).first() is not None
