from datetime import datetime
import re

from app import db
from app.models import AnneeScolaire, Classe, Ecole, EcoleNiveauConfig, NiveauScolaire


STANDARD_NIVEAUX = [
    ("CI", "CI", "primaire"),
    ("CP", "CP", "primaire"),
    ("CE1", "CE1", "primaire"),
    ("CE2", "CE2", "primaire"),
    ("CM1", "CM1", "primaire"),
    ("CM2", "CM2", "primaire"),
    ("6E", "6e", "college"),
    ("5E", "5e", "college"),
    ("4E", "4e", "college"),
    ("3E", "3e", "college"),
    ("2NDE", "2nde", "lycee"),
    ("1ERE", "1ere", "lycee"),
    ("TERMINALE", "Terminale", "lycee"),
]

SECONDARY_CYCLES = ("college", "lycee")


def ensure_standard_niveaux(commit=False):
    niveaux = {}
    for ordre, (code, nom, cycle) in enumerate(STANDARD_NIVEAUX, start=1):
        niveau = NiveauScolaire.query.filter_by(code=code).first()
        if not niveau:
            niveau = NiveauScolaire(code=code, nom=nom, cycle=cycle, ordre=ordre)
            db.session.add(niveau)
        else:
            niveau.nom = nom
            niveau.cycle = cycle
            niveau.ordre = ordre
        niveaux[code] = niveau

    db.session.flush()
    for index, (code, _nom, _cycle) in enumerate(STANDARD_NIVEAUX):
        niveau = niveaux[code]
        niveau.niveau_suivant_id = niveaux[STANDARD_NIVEAUX[index + 1][0]].id if index + 1 < len(STANDARD_NIVEAUX) else None

    if commit:
        db.session.commit()
    return [niveaux[code] for code, _nom, _cycle in STANDARD_NIVEAUX]


def ensure_ecole_niveau_configs(ecole_id, default_active=True, commit=False):
    niveaux = ensure_standard_niveaux(commit=False)
    existing = {
        config.niveau_id: config
        for config in EcoleNiveauConfig.query.filter_by(ecole_id=ecole_id).all()
    }
    now = datetime.utcnow()
    for niveau in niveaux:
        if niveau.id not in existing:
            db.session.add(EcoleNiveauConfig(
                ecole_id=ecole_id,
                niveau_id=niveau.id,
                actif=default_active,
                date_activation=now if default_active else None,
                date_desactivation=None if default_active else now,
            ))
    if commit:
        db.session.commit()
    return EcoleNiveauConfig.query.join(NiveauScolaire).filter(
        EcoleNiveauConfig.ecole_id == ecole_id
    ).order_by(NiveauScolaire.ordre).all()


def ensure_configs_for_existing_ecoles(commit=False):
    for ecole in Ecole.query.all():
        ensure_ecole_niveau_configs(ecole.id, default_active=True, commit=False)
    if commit:
        db.session.commit()


def get_niveaux_actifs(ecole_id):
    ensure_ecole_niveau_configs(ecole_id, default_active=True, commit=False)
    return NiveauScolaire.query.join(EcoleNiveauConfig).filter(
        EcoleNiveauConfig.ecole_id == ecole_id,
        EcoleNiveauConfig.actif.is_(True),
    ).order_by(NiveauScolaire.ordre).all()


def get_niveau_configs_grouped(ecole_id):
    configs = ensure_ecole_niveau_configs(ecole_id, default_active=True, commit=False)
    grouped = {"primaire": [], "college": [], "lycee": []}
    for config in configs:
        grouped.setdefault(config.niveau.cycle, []).append(config)
    return grouped


def set_niveau_actif(ecole_id, niveau_id, actif):
    config = EcoleNiveauConfig.query.filter_by(ecole_id=ecole_id, niveau_id=niveau_id).first()
    if not config:
        ensure_ecole_niveau_configs(ecole_id, default_active=True, commit=False)
        config = EcoleNiveauConfig.query.filter_by(ecole_id=ecole_id, niveau_id=niveau_id).first()
    if not config:
        return None, "Niveau introuvable pour cet etablissement."

    now = datetime.utcnow()
    config.actif = bool(actif)
    if config.actif:
        config.date_activation = now
        config.date_desactivation = None
    else:
        config.date_desactivation = now
    db.session.commit()
    return config, None


def set_cycle_actif(ecole_id, cycle_key, actif):
    ensure_ecole_niveau_configs(ecole_id, default_active=True, commit=False)
    cycles = ("primaire",) if cycle_key == "primaire" else SECONDARY_CYCLES
    configs = EcoleNiveauConfig.query.join(NiveauScolaire).filter(
        EcoleNiveauConfig.ecole_id == ecole_id,
        NiveauScolaire.cycle.in_(cycles),
    ).all()
    now = datetime.utcnow()
    for config in configs:
        config.actif = bool(actif)
        if config.actif:
            config.date_activation = now
            config.date_desactivation = None
        else:
            config.date_desactivation = now
    db.session.commit()
    return configs


def niveau_peut_etre_utilise(ecole_id, niveau_id):
    return EcoleNiveauConfig.query.filter_by(
        ecole_id=ecole_id,
        niveau_id=niveau_id,
        actif=True,
    ).first() is not None


def proposer_nom_classe(niveau, section):
    niveau_nom = niveau.nom if hasattr(niveau, "nom") else str(niveau or "")
    section = (section or "").strip()
    return f"{niveau_nom} {section}".strip()


def creer_classe_depuis_niveau(ecole_id, annee_scolaire_id, niveau_id, nom=None, section=None, salle=None, capacite=35, professeur_id=None):
    annee = AnneeScolaire.query.filter_by(id=annee_scolaire_id, ecole_id=ecole_id).first()
    if not annee:
        return None, "L'annee scolaire specifiee est invalide pour cet etablissement."
    if annee.statut == "archivee":
        return None, "Impossible de creer une classe dans une annee scolaire archivee."

    niveau = db.session.get(NiveauScolaire, niveau_id) if niveau_id else None
    if not niveau:
        return None, "Veuillez choisir un niveau scolaire valide."
    if not niveau_peut_etre_utilise(ecole_id, niveau.id):
        return None, f"Le niveau {niveau.nom} est desactive pour cet etablissement."

    section = (section or "").strip() or None
    nom = (nom or proposer_nom_classe(niveau, section)).strip()
    if not nom:
        return None, "Le nom de la classe est obligatoire."

    try:
        capacite = int(capacite)
        if capacite <= 0:
            capacite = 35
    except (ValueError, TypeError):
        capacite = 35

    existing = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id, nom=nom).first()
    if existing:
        return None, "Une classe avec ce nom existe deja pour cette annee scolaire."

    classe = Classe(
        nom=nom,
        niveau=niveau.nom,
        niveau_id=niveau.id,
        section=section,
        salle=(salle or "").strip() or None,
        capacite=capacite,
        capacite_max=capacite,
        effectif=0,
        statut="ouverte",
        professeur_id=professeur_id,
        annee_scolaire_id=annee.id,
        ecole_id=ecole_id,
    )
    db.session.add(classe)
    db.session.commit()
    return classe, None


def modifier_classe_depuis_niveau(classe, ecole_id, niveau_id, nom=None, section=None, capacite=35, professeur_id=None):
    if not classe or classe.ecole_id != ecole_id:
        return None, "Classe introuvable pour cet etablissement."

    annee = classe.annee_scolaire
    if not annee or annee.ecole_id != ecole_id:
        return None, "L'annee scolaire de cette classe est invalide pour cet etablissement."
    if annee.statut == "archivee":
        return None, "Impossible de modifier une classe d'une annee scolaire archivee."

    niveau = db.session.get(NiveauScolaire, niveau_id) if niveau_id else None
    if not niveau:
        return None, "Veuillez choisir un niveau scolaire valide."
    if not niveau_peut_etre_utilise(ecole_id, niveau.id):
        return None, f"Le niveau {niveau.nom} est desactive pour cet etablissement."

    section = (section or "").strip() or None
    nom = (nom or proposer_nom_classe(niveau, section)).strip()
    if not nom:
        return None, "Le nom de la classe est obligatoire."

    existing = Classe.query.filter(
        Classe.ecole_id == ecole_id,
        Classe.annee_scolaire_id == annee.id,
        Classe.nom == nom,
        Classe.id != classe.id,
    ).first()
    if existing:
        return None, "Une classe avec ce nom existe deja pour cette annee scolaire."

    try:
        capacite = int(capacite)
        if capacite <= 0:
            capacite = 35
    except (ValueError, TypeError):
        capacite = classe.capacite or classe.capacite_max or 35

    classe.nom = nom
    classe.niveau = niveau.nom
    classe.niveau_id = niveau.id
    classe.section = section
    classe.capacite = capacite
    classe.capacite_max = capacite
    classe.effectif = classe.effectif_reel
    classe.professeur_id = professeur_id
    db.session.commit()
    return classe, None


def infer_niveau_from_classe_name(nom):
    value = (nom or "").strip().upper()
    replacements = {
        "6EME": "6E",
        "6EME": "6E",
        "6ÈME": "6E",
        "5EME": "5E",
        "5ÈME": "5E",
        "4EME": "4E",
        "4ÈME": "4E",
        "3EME": "3E",
        "3ÈME": "3E",
        "SECONDE": "2NDE",
        "2NDE": "2NDE",
        "TLE": "TERMINALE",
        "TERM": "TERMINALE",
        "1ÈRE": "1ERE",
        "1ERE": "1ERE",
    }
    first_token = re.split(r"[\s\-_/]+", value)[0] if value else ""
    normalized = replacements.get(first_token, first_token)
    if normalized == "2NDE":
        return "2NDE"
    valid_codes = {code for code, _nom, _cycle in STANDARD_NIVEAUX}
    return normalized if normalized in valid_codes else None
