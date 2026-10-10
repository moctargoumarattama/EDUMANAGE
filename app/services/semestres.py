from datetime import timedelta

from sqlalchemy.orm import joinedload

from app import db
from app.models import Absence, AnneeScolaire, Bulletin, Inscription, PeriodeBulletin, Presence
from app.services.niveaux import PERIODES_PRIMAIRE_DEFAULT, determiner_cycle_classe


SEMESTRE_1 = "Semestre 1"
SEMESTRE_2 = "Semestre 2"
SEMESTRES = (SEMESTRE_1, SEMESTRE_2)
COMPOSITIONS_PRIMAIRES = tuple(PERIODES_PRIMAIRE_DEFAULT)
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


def get_compositions_annee(ecole_id, annee_id):
    """Retourne les trois périodes primaires dans leur ordre chronologique."""
    if not ecole_id or not annee_id:
        return []
    periodes = PeriodeBulletin.query.filter(
        PeriodeBulletin.ecole_id == ecole_id,
        PeriodeBulletin.annee_id == annee_id,
        PeriodeBulletin.nom.in_(COMPOSITIONS_PRIMAIRES),
    ).all()
    ordre = {nom.casefold(): index for index, nom in enumerate(COMPOSITIONS_PRIMAIRES)}
    return sorted(periodes, key=lambda p: (ordre.get((p.nom or '').casefold(), 99), p.id or 0))


def calendrier_configure_compositions(ecole_id, annee_id):
    """Valide les trois compositions primaires et leurs bornes."""
    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    compositions = get_compositions_annee(ecole_id, annee_id)
    if not annee or len(compositions) != 3:
        return False
    return (
        all(
            p.date_debut and p.date_fin
            and annee.date_debut <= p.date_debut <= p.date_fin <= annee.date_fin
            for p in compositions
        )
        and all(a.date_fin < b.date_debut for a, b in zip(compositions, compositions[1:]))
    )


def calendrier_configure_mixte(ecole_id, annee_id):
    """Vérifie qu'un groupe scolaire mixte possède les deux calendriers."""
    return calendrier_configure(ecole_id, annee_id) and calendrier_configure_compositions(ecole_id, annee_id)


def calculer_bornes_compositions(annee, fins):
    """Construit des périodes contiguës à partir des trois dates de fin."""
    if not annee or not annee.date_debut or not annee.date_fin:
        return None, "Les dates de l'année scolaire sont obligatoires."
    if not fins or len(fins) != 3 or any(not date_fin for date_fin in fins):
        return None, "Les dates de fin des trois compositions sont obligatoires."
    if any(fins[index] >= fins[index + 1] for index in range(2)):
        return None, "Les trois compositions doivent être dans l'ordre chronologique."
    if not all(annee.date_debut < date_fin < annee.date_fin for date_fin in fins):
        return None, "Les dates des compositions doivent être comprises dans l'année scolaire."
    bornes = {}
    debut = annee.date_debut
    for nom, fin in zip(COMPOSITIONS_PRIMAIRES, fins):
        bornes[nom] = (debut, fin)
        debut = fin + timedelta(days=1)
    return bornes, None


def configurer_compositions_annee(ecole_id, annee_id, fins, *, force=False):
    """Enregistre les trois compositions primaires sans modifier le schéma."""
    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        return None, "Année scolaire introuvable ou non autorisée."
    if annee.statut == "archivee":
        return None, MESSAGE_ARCHIVE_LECTURE_SEULE
    bornes, err = calculer_bornes_compositions(annee, fins)
    if err:
        return None, err
    existantes = {
        p.nom: p for p in PeriodeBulletin.query.filter_by(
            ecole_id=ecole_id, annee_id=annee_id
        ).filter(PeriodeBulletin.nom.in_(COMPOSITIONS_PRIMAIRES)).all()
    }
    if annee.statut == "active" and any(p.publie for p in existantes.values()) and not force:
        return None, "Modification refusée : une composition est déjà publiée."
    periodes = []
    for nom in COMPOSITIONS_PRIMAIRES:
        periode = existantes.get(nom)
        if not periode:
            periode = PeriodeBulletin(
                nom=nom, annee_id=annee_id, ecole_id=ecole_id,
                publie=False, periode_active=False,
            )
            db.session.add(periode)
        periode.date_debut, periode.date_fin = bornes[nom]
        periodes.append(periode)
    db.session.commit()
    return periodes, None


def calendrier_configure(ecole_id, annee_id):
    periodes = get_semestres_annee(ecole_id, annee_id)
    annee = db.session.get(AnneeScolaire, annee_id)
    return bool(
        annee and len(periodes) == 2
        and all(p.date_debut and p.date_fin and annee.date_debut <= p.date_debut <= p.date_fin <= annee.date_fin
                for p in periodes)
        and periodes[0].date_fin < periodes[1].date_debut
    )


def calendrier_configure_pour_cycles(ecole_id, annee_id):
    """Vérifie les périodes nécessaires aux classes réellement préparées."""
    from app.models import Classe
    from app.services.niveaux import determiner_cycle_classe

    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        return False, 'une année scolaire valide'
    classes = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_id).all()
    primaire = any(determiner_cycle_classe(classe) == 'primaire' for classe in classes)
    secondaire = not classes or any(determiner_cycle_classe(classe) != 'primaire' for classe in classes)
    manquants = []

    if secondaire and not calendrier_configure(ecole_id, annee_id):
        manquants.append('les semestres S1 et S2')
    if primaire:
        periodes = PeriodeBulletin.query.filter_by(ecole_id=ecole_id, annee_id=annee_id).all()
        compositions = [p for p in periodes if p.nom and (
            'composition' in p.nom.casefold() or 'trimestre' in p.nom.casefold()
        )]
        compositions.sort(key=lambda p: (p.date_debut or annee.date_fin, p.id or 0))
        if (len(compositions) != 3 or len({p.nom.strip().casefold() for p in compositions}) != 3
                or not all(p.date_debut and p.date_fin
                           and annee.date_debut <= p.date_debut <= p.date_fin <= annee.date_fin
                           for p in compositions)
                or any(a.date_fin >= b.date_debut for a, b in zip(compositions, compositions[1:]))):
            manquants.append('les trois compositions primaires datées')

    return not manquants, ', '.join(manquants)


def calculer_bornes_semestres(annee, fin_semestre_1):
    if not annee:
        return None, "Année scolaire introuvable."
    if not fin_semestre_1:
        return None, "La fin du Semestre 1 est obligatoire."
    if not annee.date_debut or not annee.date_fin:
        return None, "Les dates de l'année scolaire sont obligatoires."
    if not (annee.date_debut < fin_semestre_1 < annee.date_fin):
        return None, (
            f"La date de fin du Semestre 1 ({fin_semestre_1.strftime('%d/%m/%Y')}) doit être strictement "
            f"comprise dans l'intervalle de l'année scolaire {annee.nom} (du {annee.date_debut.strftime('%d/%m/%Y')} au {annee.date_fin.strftime('%d/%m/%Y')})."
        )

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
