import re
from datetime import datetime
from types import SimpleNamespace

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


def get_niveau_configs_grouped(ecole_id, default_active=True):
    configs = ensure_ecole_niveau_configs(ecole_id, default_active=default_active, commit=False)
    grouped = {"primaire": [], "college": [], "lycee": []}
    for config in configs:
        grouped.setdefault(config.niveau.cycle, []).append(config)
    return grouped


def get_niveau_options_grouped(ecole_id):
    niveaux = ensure_standard_niveaux(commit=False)
    existing = {
        config.niveau_id: config
        for config in EcoleNiveauConfig.query.filter_by(ecole_id=ecole_id).all()
    }
    grouped = {"primaire": [], "college": [], "lycee": []}
    for niveau in niveaux:
        config = existing.get(niveau.id)
        grouped.setdefault(niveau.cycle, []).append(SimpleNamespace(
            niveau_id=niveau.id,
            actif=bool(config.actif) if config else False,
            niveau=niveau,
        ))
    return grouped


def configurer_niveaux_ecole(ecole_id, niveau_ids):
    niveaux = ensure_standard_niveaux(commit=False)
    valid_ids = {niveau.id for niveau in niveaux}
    selected_ids = {int(niveau_id) for niveau_id in (niveau_ids or []) if str(niveau_id).isdigit()}
    selected_ids = selected_ids.intersection(valid_ids)
    if not selected_ids:
        return None, "Veuillez selectionner au moins un niveau scolaire."

    configs = ensure_ecole_niveau_configs(ecole_id, default_active=False, commit=False)
    now = datetime.utcnow()
    for config in configs:
        config.actif = config.niveau_id in selected_ids
        if config.actif:
            config.date_activation = now
            config.date_desactivation = None
        else:
            config.date_desactivation = now
    db.session.flush()
    return configs, None


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


SECTION_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]$")


def normaliser_section_classe(section):
    raw_section = section or ""
    section = raw_section.strip()
    if raw_section != section or not SECTION_RE.fullmatch(section):
        return None, "La section/serie doit contenir exactement une seule lettre."
    return section.upper(), None


def libelle_section_niveau(niveau):
    return "Serie" if getattr(niveau, "cycle", None) == "lycee" else "Section"


def proposer_nom_classe(niveau, section):
    niveau_nom = niveau.nom if hasattr(niveau, "nom") else str(niveau or "")
    section = (section or "").strip().upper()
    if getattr(niveau, "cycle", None) == "lycee":
        return f"{niveau_nom} Serie {section}".strip()
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
    from app.services.structure_annuelle import niveau_est_dans_structure
    if not niveau_est_dans_structure(ecole_id, annee.id, niveau.id):
        return None, f"Le niveau {niveau.nom} n'est pas retenu pour cette annee scolaire."

    section, error = normaliser_section_classe(section)
    if error:
        return None, error
    nom = proposer_nom_classe(niveau, section)

    try:
        capacite = int(capacite)
        if capacite <= 0:
            capacite = 35
    except (ValueError, TypeError):
        capacite = 35

    existing = Classe.query.filter_by(
        ecole_id=ecole_id,
        annee_scolaire_id=annee.id,
        niveau_id=niveau.id,
        section=section,
    ).first()
    if existing:
        return None, "Une classe existe deja pour ce niveau et cette section dans cette annee scolaire."

    existing_name = Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee.id, nom=nom).first()
    if existing_name:
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
    from app.services.structure_annuelle import niveau_est_dans_structure
    if not niveau_est_dans_structure(ecole_id, annee.id, niveau.id):
        return None, f"Le niveau {niveau.nom} n'est pas retenu pour cette annee scolaire."

    section, error = normaliser_section_classe(section)
    if error:
        return None, error
    nom = proposer_nom_classe(niveau, section)

    existing = Classe.query.filter(
        Classe.ecole_id == ecole_id,
        Classe.annee_scolaire_id == annee.id,
        Classe.niveau_id == niveau.id,
        Classe.section == section,
        Classe.id != classe.id,
    ).first()
    if existing:
        return None, "Une classe existe deja pour ce niveau et cette section dans cette annee scolaire."

    existing_name = Classe.query.filter(
        Classe.ecole_id == ecole_id,
        Classe.annee_scolaire_id == annee.id,
        Classe.nom == nom,
        Classe.id != classe.id,
    ).first()
    if existing_name:
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
    from app.services.classes_annuelles import precharger_effectif_classe
    precharger_effectif_classe(classe)
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
