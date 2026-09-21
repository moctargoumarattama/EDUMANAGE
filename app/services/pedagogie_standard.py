"""Service canonique de pédagogie standard et accélération du démarrage d'école.

Fournit :
1. Le catalogue des matières standards et coefficients par niveau / cycle / série.
2. La génération de classes par lot (idempotente et protégée par école et année).
3. L'injection automatique des matières standards par classe.
4. L'évaluation de l'état d'avancement (guidance state) pour le tableau de bord.
"""

from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.models import AnneeScolaire, Classe, Cours, Inscription, NiveauScolaire
from app.services.cours_uniqueness import find_duplicate_cours
from app.services.niveaux import (
    infer_niveau_from_classe_name,
    normaliser_section_classe,
    proposer_nom_classe,
)
from app.services.structure_annuelle import niveau_est_dans_structure


# -----------------------------------------------------------------------------
# 1. CATALOGUE OFFICIEL DES MATIÈRES STANDARDS ET COEFFICIENTS
# -----------------------------------------------------------------------------

CATALOGUE_MATIERES_STANDARD: Dict[str, List[Tuple[str, float]]] = {
    # Primaire (CI à CM2)
    "primaire": [
        ("Français", 4.0),
        ("Mathématiques", 4.0),
        ("Éveil / Sciences", 2.0),
        ("Histoire-Géographie", 1.5),
        ("Éducation Civique", 1.0),
        ("EPS", 1.0),
    ],
    # Collège (6e, 5e, 4e, 3e)
    "college": [
        ("Français", 4.0),
        ("Mathématiques", 4.0),
        ("Anglais", 2.0),
        ("Histoire-Géographie", 2.0),
        ("Physique-Chimie", 2.0),
        ("Sciences de la Vie et de la Terre (SVT)", 2.0),
        ("Éducation Physique et Sportive (EPS)", 1.0),
        ("Informatique", 1.0),
    ],
    # Lycée Seconde Générale (2nde)
    "lycee_2nde": [
        ("Français", 4.0),
        ("Mathématiques", 4.0),
        ("Histoire-Géographie", 3.0),
        ("Anglais", 3.0),
        ("Physique-Chimie", 3.0),
        ("Sciences de la Vie et de la Terre (SVT)", 3.0),
        ("Éducation Physique et Sportive (EPS)", 1.0),
        ("Informatique", 1.0),
    ],
    # Lycée 1ère & Terminale - Série A (Littéraire)
    "lycee_serie_a": [
        ("Philosophie", 5.0),
        ("Français / Littérature", 5.0),
        ("Histoire-Géographie", 4.0),
        ("Anglais", 3.0),
        ("LV2", 2.0),
        ("Mathématiques", 2.0),
        ("Éducation Physique et Sportive (EPS)", 1.0),
    ],
    # Lycée 1ère & Terminale - Série C (Scientifique Mathématiques / Sciences Physiques)
    "lycee_serie_c": [
        ("Mathématiques", 5.0),
        ("Physique-Chimie", 5.0),
        ("Sciences de la Vie et de la Terre (SVT)", 3.0),
        ("Français / Philosophie", 3.0),
        ("Anglais", 2.0),
        ("Histoire-Géographie", 2.0),
        ("Éducation Physique et Sportive (EPS)", 1.0),
    ],
    # Lycée 1ère & Terminale - Série D (Scientifique Sciences Naturelles)
    "lycee_serie_d": [
        ("Mathématiques", 4.0),
        ("Physique-Chimie", 4.0),
        ("Sciences de la Vie et de la Terre (SVT)", 4.0),
        ("Français / Philosophie", 3.0),
        ("Anglais", 2.0),
        ("Histoire-Géographie", 2.0),
        ("Éducation Physique et Sportive (EPS)", 1.0),
    ],
    # Fallback générique
    "generique": [
        ("Français", 4.0),
        ("Mathématiques", 4.0),
        ("Anglais", 2.0),
        ("Histoire-Géographie", 2.0),
        ("Sciences", 2.0),
        ("Éducation Physique et Sportive (EPS)", 1.0),
    ],
}


def obtenir_matieres_standard(niveau_code: Optional[str], cycle: Optional[str] = None, serie: Optional[str] = None) -> List[Tuple[str, float]]:
    """Résout et retourne la liste des matières et coefficients recommandés pour un niveau donné."""
    code = (niveau_code or "").strip().upper()
    cycle_norm = (cycle or "").strip().lower()
    serie_norm = (serie or "").strip().upper()

    # Primaire
    if cycle_norm == "primaire" or code in {"CI", "CP", "CE1", "CE2", "CM1", "CM2"}:
        return list(CATALOGUE_MATIERES_STANDARD["primaire"])

    # Collège
    if cycle_norm == "college" or code in {"6E", "5E", "4E", "3E"}:
        return list(CATALOGUE_MATIERES_STANDARD["college"])

    # Lycée Seconde
    if code in {"2NDE", "SECONDE"}:
        return list(CATALOGUE_MATIERES_STANDARD["lycee_2nde"])

    # Lycée 1ère ou Terminale par séries
    if cycle_norm == "lycee" or code in {"1ERE", "TERMINALE", "TLE"}:
        if serie_norm in {"A", "A1", "A2", "L", "LITTERAIRE"}:
            return list(CATALOGUE_MATIERES_STANDARD["lycee_serie_a"])
        if serie_norm in {"C", "SM"}:
            return list(CATALOGUE_MATIERES_STANDARD["lycee_serie_c"])
        if serie_norm in {"D", "S", "SE", "SCIENTIFIQUE"}:
            return list(CATALOGUE_MATIERES_STANDARD["lycee_serie_d"])
        # Si série non précisée en 1ère/Tle, catalogue Seconde ou Générique
        return list(CATALOGUE_MATIERES_STANDARD["lycee_2nde"])

    return list(CATALOGUE_MATIERES_STANDARD["generique"])


# -----------------------------------------------------------------------------
# 2. GÉNÉRATION DE CLASSES PAR LOT (BATCH)
# -----------------------------------------------------------------------------

def generer_classes_batch(
    ecole_id: int,
    annee_id: int,
    configurations: List[Dict[str, Any]],
) -> Tuple[List[Classe], List[Classe], Optional[str]]:
    """
    Génère un lot de classes pour une école et une année scolaire données.

    :param ecole_id: ID de l'établissement
    :param annee_id: ID de l'année scolaire active/cible
    :param configurations: Liste de configurations {'niveau_id': int, 'section': str, 'capacite': Optional[int]}
    :return: (classes_creees, classes_existantes, erreur_eventuelle)
    """
    if not ecole_id or not annee_id:
        return [], [], "Identifiants école ou année manquants."

    annee = AnneeScolaire.query.filter_by(id=annee_id, ecole_id=ecole_id).first()
    if not annee:
        return [], [], "Année scolaire introuvable pour cet établissement."
    if annee.statut == "archivee":
        return [], [], "Impossible de créer des classes dans une année scolaire archivée."

    if not configurations:
        return [], [], "Aucune configuration de classe fournie."

    classes_creees: List[Classe] = []
    classes_existantes: List[Classe] = []

    for conf in configurations:
        niveau_id = conf.get("niveau_id")
        section_raw = conf.get("section")
        capacite_raw = conf.get("capacite", 35)

        niveau = db.session.get(NiveauScolaire, niveau_id) if niveau_id else None
        if not niveau:
            continue

        # Vérifier que le niveau fait partie de la structure annuelle retenue
        if not niveau_est_dans_structure(ecole_id, annee.id, niveau.id):
            continue

        section, err = normaliser_section_classe(section_raw)
        if err:
            continue

        nom = proposer_nom_classe(niveau, section)

        try:
            capacite = int(capacite_raw)
            if capacite <= 0:
                capacite = 35
        except (ValueError, TypeError):
            capacite = 35

        # Recherche de doublons
        existing = Classe.query.filter_by(
            ecole_id=ecole_id,
            annee_scolaire_id=annee.id,
            nom=nom,
        ).first()

        if not existing:
            existing = Classe.query.filter_by(
                ecole_id=ecole_id,
                annee_scolaire_id=annee.id,
                niveau_id=niveau.id,
                section=section,
            ).first()

        if existing:
            classes_existantes.append(existing)
            continue

        nouvelle_classe = Classe(
            nom=nom,
            niveau=niveau.nom,
            niveau_id=niveau.id,
            section=section,
            capacite=capacite,
            capacite_max=capacite,
            effectif=0,
            statut="ouverte",
            professeur_id=None,
            annee_scolaire_id=annee.id,
            ecole_id=ecole_id,
        )
        db.session.add(nouvelle_classe)
        classes_creees.append(nouvelle_classe)

    if classes_creees:
        db.session.commit()

    return classes_creees, classes_existantes, None


# -----------------------------------------------------------------------------
# 3. INJECTION AUTOMATIQUE DES MATIÈRES STANDARDS
# -----------------------------------------------------------------------------

def injecter_matieres_standard(
    ecole_id: int,
    annee_id: Optional[int],
    classe_id: int,
) -> Tuple[List[Cours], Optional[str]]:
    """
    Injecte les cours et coefficients standards dans une classe spécifique.
    Ignore les cours déjà présents (idempotent, sans doublons).

    :param ecole_id: ID de l'établissement
    :param annee_id: ID de l'année scolaire (optionnel, pour double vérification)
    :param classe_id: ID de la classe cible
    :return: (cours_crees, erreur_eventuelle)
    """
    classe = Classe.query.filter_by(id=classe_id, ecole_id=ecole_id).first()
    if not classe:
        return [], "Classe introuvable pour cet établissement."

    if annee_id and classe.annee_scolaire_id != annee_id:
        return [], "La classe n'appartient pas à l'année scolaire indiquée."

    if classe.annee_scolaire and classe.annee_scolaire.statut == "archivee":
        return [], "Impossible de modifier les cours d'une classe d'une année archivée."

    # Détection du niveau et cycle
    niveau = classe.niveau_scolaire
    if not niveau and classe.niveau_id:
        niveau = db.session.get(NiveauScolaire, classe.niveau_id)

    cycle = niveau.cycle if niveau else None
    code = niveau.code if niveau else (infer_niveau_from_classe_name(classe.nom) or classe.niveau)
    serie = classe.section

    matieres_recommandees = obtenir_matieres_standard(code, cycle=cycle, serie=serie)

    cours_existants = Cours.query.filter_by(ecole_id=ecole_id, classe_id=classe.id).all()
    existing_normalized = {" ".join(c.nom.split()).lower() for c in cours_existants if c.nom}

    cours_crees: List[Cours] = []
    for nom_matiere, coeff in matieres_recommandees:
        norm_nom = " ".join(nom_matiere.split()).lower()
        if norm_nom in existing_normalized:
            continue

        cours = Cours(
            nom=nom_matiere,
            coefficient=coeff,
            ecole_id=ecole_id,
            classe_id=classe.id,
            professeur_id=None,
        )
        db.session.add(cours)
        cours_crees.append(cours)
        existing_normalized.add(norm_nom)

    if cours_crees:
        db.session.commit()

    return cours_crees, None


# -----------------------------------------------------------------------------
# 4. ÉTAT D'AVANCEMENT POUR LE GUIDAGE PAS-À-PAS DU DASHBOARD
# -----------------------------------------------------------------------------

def get_school_guidance_state(ecole_id: int, annee_id: Optional[int]) -> Dict[str, Any]:
    """
    Calcule l'état d'avancement de la configuration pédagogique pour l'école et l'année données.

    Règles :
    - has_classes : au moins 1 classe créée pour l'année
    - has_cours : au moins 1 cours rattaché à une classe de l'année
    - has_eleves : au moins 1 élève inscrit (statut != 'desinscrit') pour l'année
    - setup_complet : has_classes and has_cours and has_eleves
    - current_step : 1 (classes), 2 (matières/cours), 3 (élèves), ou 'complete'
    """
    if not ecole_id or not annee_id:
        return {
            "has_classes": False,
            "has_cours": False,
            "has_eleves": False,
            "setup_complet": False,
            "current_step": 1,
        }

    has_classes = (
        Classe.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_id).first()
        is not None
    )

    has_cours = (
        Cours.query.join(Classe, Classe.id == Cours.classe_id)
        .filter(Cours.ecole_id == ecole_id, Classe.annee_scolaire_id == annee_id)
        .first()
        is not None
    )

    has_eleves = (
        Inscription.query.filter_by(ecole_id=ecole_id, annee_scolaire_id=annee_id)
        .filter(Inscription.statut != "desinscrit")
        .first()
        is not None
    )

    setup_complet = bool(has_classes and has_cours and has_eleves)

    if not has_classes:
        current_step = 1
    elif not has_cours:
        current_step = 2
    elif not has_eleves:
        current_step = 3
    else:
        current_step = "complete"

    return {
        "has_classes": has_classes,
        "has_cours": has_cours,
        "has_eleves": has_eleves,
        "setup_complet": setup_complet,
        "current_step": current_step,
    }
