# app/utils.py
"""
Utilitaires pour la gestion sécurisée multi-écoles
Fonctions helpers respectant l'isolation des données
"""

from flask import current_app, jsonify, send_file, flash, g
from flask_login import current_user
from datetime import datetime, date, timedelta
from urllib.parse import urlsplit
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import selectinload, joinedload
import csv
import io
import json
import traceback
from typing import Optional, List, Dict, Any, Union
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.models import AnneeScolaire


def is_safe_internal_url(value: str) -> bool:
    """Return True only for local application paths such as /eleves?search=a."""
    if not value or not isinstance(value, str):
        return False

    value = value.strip()
    if not value.startswith("/") or value.startswith("//") or value.startswith("\\"):
        return False
    if "\\" in value:
        return False

    parts = urlsplit(value)
    return not parts.scheme and not parts.netloc


def sanitize_internal_url(value: str, fallback: str = "/") -> str:
    return value.strip() if is_safe_internal_url(value) else fallback


# ====================================================================
# 🏫 GESTION ÉCOLE (Fonctions unifiées avec middleware)
# ====================================================================

def get_ecole_filter_query(model):
    """
    Retourne une query filtrée selon l'école de l'utilisateur.
    
    Args:
        model: Modèle SQLAlchemy à filtrer
    
    Returns:
        Query filtrée par école ou query complète pour super-admin
    """
    from app.middleware import get_ecole_courante
    
    query = model.query
    
    # Super-admin voit tout
    if current_user.is_authenticated and current_user.role == 'super_admin':
        # Si une école est sélectionnée en session, on peut filtrer
        ecole = get_ecole_courante()
        if ecole and not isinstance(ecole, tuple):
            if hasattr(model, 'ecole_id'):
                return query.filter(model.ecole_id == ecole.id)
        return query
    
    # Utilisateur normal : filtrage strict
    if not current_user.is_authenticated or not current_user.ecole_id:
        # Pas d'école = pas de données
        return query.filter(False)
    
    # Filtrer par l'école de l'utilisateur
    if hasattr(model, 'ecole_id'):
        return query.filter(model.ecole_id == current_user.ecole_id)
    
    # Modèle sans ecole_id : logger et retourner vide par sécurité
    current_app.logger.warning(
        f"[get_ecole_filter_query] {model.__name__} n'a pas de champ ecole_id"
    )
    return query.filter(False)


def ensure_ecole_consistency(obj, ecole_id=None):
    """
    S'assure qu'un objet a le bon ecole_id.
    
    Args:
        obj: Objet à vérifier/corriger
        ecole_id: ID de l'école (optionnel, sinon utilise l'école courante)
    
    Returns:
        bool: True si cohérent ou corrigé, False sinon
    """
    if not hasattr(obj, 'ecole_id'):
        return True  # Pas de champ ecole_id, on considère OK
    
    target_ecole_id = ecole_id or get_ecole_id()
    if not target_ecole_id:
        current_app.logger.error(f"Impossible de déterminer l'école pour {obj}")
        return False
    
    if obj.ecole_id != target_ecole_id:
        current_app.logger.warning(
            f"Correction ecole_id: {obj.__class__.__name__} ID={getattr(obj, 'id', '')} "
            f"({obj.ecole_id} -> {target_ecole_id})"
        )
        obj.ecole_id = target_ecole_id
    
    return True


# ====================================================================
# 📅 GESTION ANNÉES SCOLAIRES
# ====================================================================

def get_annee_active(ecole_id=None) -> Optional['AnneeScolaire']:
    """
    Retourne l'année scolaire active pour une école.
    
    Args:
        ecole_id: ID de l'école (optionnel)
    
    Returns:
        AnneeScolaire ou None
    """
    from app.models import AnneeScolaire
    from app.middleware import get_ecole_id
    
    ecole_id = ecole_id or get_ecole_id()
    if not ecole_id:
        return None

    try:
        if not hasattr(g, '_annee_active_cache'):
            g._annee_active_cache = {}
        if ecole_id in g._annee_active_cache:
            return g._annee_active_cache[ecole_id]
    except RuntimeError:
        pass
    
    annee = AnneeScolaire.query.filter_by(
        ecole_id=ecole_id,
        statut='active'
    ).first()

    try:
        if hasattr(g, '_annee_active_cache'):
            g._annee_active_cache[ecole_id] = annee
    except RuntimeError:
        pass

    return annee


_ecoles_identite_validee = set()


def get_school_setup_state(ecole_id=None, force_refresh: bool = False) -> Dict[str, Any]:
    """Évalue l'état d'avancement de la configuration initiale d'une école.

    Enchaînement canonique des étapes (wizard /onboarding) :
    1. 'year' : Année scolaire active créée
    2. 'semestres' : Périodes / semestres configurés
    3. 'pedagogie' : Niveaux scolaires activés
    4. 'classes' : Au moins une classe créée pour l'année active
    5. 'matieres' : Au moins un cours / matière configuré pour les classes
    6. 'identite' : Coordonnées, ville, slogan et logo configurés
    7. 'complete' : Configuration terminée, accès au dashboard

    Returns:
        dict avec setup_complete et current_step.
    """
    from app.models import AnneeNiveauConfig, AnneeScolaire, Classe, Cours, NiveauScolaire, Ecole
    from app.middleware import get_ecole_id
    from app.services.semestres import calendrier_configure

    target_ecole_id = ecole_id or get_ecole_id()
    if not target_ecole_id:
        return {
            'has_active_year': False,
            'active_year': None,
            'has_class': False,
            'has_pedagogie': False,
            'has_semestres': False,
            'has_cours': False,
            'has_identite': False,
            'setup_complete': False,
            'current_step': 'year'
        }

    use_cache = not force_refresh
    try:
        if use_cache:
            if not hasattr(g, '_school_setup_cache'):
                g._school_setup_cache = {}
            if target_ecole_id in g._school_setup_cache:
                return g._school_setup_cache[target_ecole_id]
    except RuntimeError:
        pass

    ecole = Ecole.query.get(target_ecole_id)
    onboarding_complete = getattr(ecole, 'onboarding_complete', False) if ecole else False
    active_year = get_annee_active(target_ecole_id)

    if onboarding_complete:
        result = {
            'has_active_year': active_year is not None,
            'active_year': active_year,
            'has_class': True,  # Valeurs non bloquantes
            'has_pedagogie': True,
            'has_semestres': True,
            'has_cours': True,
            'has_identite': True,
            'setup_complete': True,
            'current_step': 'complete'
        }
    else:
        if not active_year:
            result = {
                'has_active_year': False,
                'active_year': None,
                'has_class': False,
                'has_pedagogie': False,
                'has_semestres': False,
                'has_cours': False,
                'has_identite': False,
                'setup_complete': False,
                'current_step': 'year'
            }
        else:
            has_pedagogie = (
                AnneeNiveauConfig.query
                .filter_by(
                    ecole_id=target_ecole_id,
                    annee_scolaire_id=active_year.id,
                    actif=True,
                )
                .first()
                is not None
            )
            has_class = Classe.query.filter_by(
                ecole_id=target_ecole_id,
                annee_scolaire_id=active_year.id
            ).with_entities(Classe.id).first() is not None
            has_semestres = calendrier_configure(target_ecole_id, active_year.id)
            has_cours = False
            if has_class:
                has_cours = (
                    Cours.query.filter_by(ecole_id=target_ecole_id)
                    .filter(
                        Cours.classe_id.in_(
                            Classe.query.filter_by(
                                ecole_id=target_ecole_id,
                                annee_scolaire_id=active_year.id,
                            ).with_entities(Classe.id)
                        )
                    )
                    .first()
                    is not None
                )

            has_identite = target_ecole_id in _ecoles_identite_validee
            if not has_identite:
                try:
                    from flask import session
                    has_identite = bool(session.get(f'onboarding_identite_done_{target_ecole_id}'))
                except (RuntimeError, AttributeError):
                    pass

            if not has_semestres:
                result = {
                    'has_active_year': True,
                    'active_year': active_year,
                    'has_class': False,
                    'has_pedagogie': False,
                    'has_semestres': False,
                    'has_cours': False,
                    'has_identite': False,
                    'setup_complete': False,
                    'current_step': 'semestres'
                }
            elif not has_pedagogie:
                result = {
                    'has_active_year': True,
                    'active_year': active_year,
                    'has_class': False,
                    'has_pedagogie': False,
                    'has_semestres': True,
                    'has_cours': False,
                    'has_identite': False,
                    'setup_complete': False,
                    'current_step': 'pedagogie'
                }
            elif not has_class:
                result = {
                    'has_active_year': True,
                    'active_year': active_year,
                    'has_class': False,
                    'has_pedagogie': True,
                    'has_semestres': True,
                    'has_cours': False,
                    'has_identite': False,
                    'setup_complete': False,
                    'current_step': 'classes'
                }
            elif not has_cours:
                result = {
                    'has_active_year': True,
                    'active_year': active_year,
                    'has_class': True,
                    'has_pedagogie': True,
                    'has_semestres': True,
                    'has_cours': False,
                    'has_identite': False,
                    'setup_complete': False,
                    'current_step': 'matieres'
                }
            elif not has_identite:
                result = {
                    'has_active_year': True,
                    'active_year': active_year,
                    'has_class': True,
                    'has_pedagogie': True,
                    'has_semestres': True,
                    'has_cours': True,
                    'has_identite': False,
                    'setup_complete': False,
                    'current_step': 'identite'
                }
            else:
                result = {
                    'has_active_year': True,
                    'active_year': active_year,
                    'has_class': True,
                    'has_pedagogie': True,
                    'has_semestres': True,
                    'has_cours': True,
                    'has_identite': True,
                    'setup_complete': False,  # Pas encore validé manuellement
                    'current_step': 'complete'
                }

    try:
        if hasattr(g, '_school_setup_cache'):
            g._school_setup_cache[target_ecole_id] = result
        else:
            g._school_setup_cache = {target_ecole_id: result}
    except RuntimeError:
        pass

    return result


def creer_ou_activer_annee_scolaire(ecole_id: int, nom: str, date_debut, date_fin):
    """
    Logique métier canonique de création ou activation d'une année scolaire.
    Garantit :
    - ecole_id imposé côté serveur
    - nom obligatoire et nettoyé
    - date_debut < date_fin
    - statut canonique 'active'
    - archivage de toute autre année active pour cette école (contrainte unicité)
    - transaction atomique
    Returns:
        (AnneeScolaire, None) en cas de succès
        (None, str) en cas d'erreur de validation ou système
    """
    from app.models import AnneeScolaire
    from app import db

    nom = (nom or '').strip()
    if not nom or not date_debut or not date_fin:
        return None, "Veuillez renseigner tous les champs obligatoires de l'année scolaire."

    import re
    match = re.match(r'^(\d{4})-(\d{4})$', nom)
    if not match or int(match.group(2)) != int(match.group(1)) + 1:
        return None, "L'année scolaire doit être de la forme AAAA-AAAA avec deux années consécutives."

    debut_annee = int(match.group(1))
    fin_annee = int(match.group(2))

    if date_fin <= date_debut:
        return None, "La date de fin doit être postérieure à la date de début."

    if date_debut.year != debut_annee:
        return None, f"La date de début doit être dans l'année {debut_annee}."

    if date_fin.year != fin_annee:
        return None, f"La date de fin doit être dans l'année {fin_annee}."

    try:
        # Désactiver toute autre année active pour cette école
        AnneeScolaire.query.filter_by(ecole_id=ecole_id, statut='active').update({'statut': 'archivee'})

        # Vérifier si une année avec ce nom existe déjà pour cette école
        existante = AnneeScolaire.query.filter_by(nom=nom, ecole_id=ecole_id).first()
        if existante:
            existante.statut = 'active'
            existante.date_debut = date_debut
            existante.date_fin = date_fin
            annee = existante
        else:
            annee = AnneeScolaire(
                nom=nom,
                date_debut=date_debut,
                date_fin=date_fin,
                statut='active',
                ecole_id=ecole_id
            )
            db.session.add(annee)

        if hasattr(g, '_school_setup_cache'):
            g._school_setup_cache.pop(ecole_id, None)
        if hasattr(g, '_annee_active_cache'):
            g._annee_active_cache.pop(ecole_id, None)
        if hasattr(g, '_annee_consultee_cache'):
            g._annee_consultee_cache.clear()
        if hasattr(g, 'annee_courante'):
            delattr(g, 'annee_courante')

        return annee, None
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f"Erreur création année scolaire : {e}")
        return None, "Une erreur est survenue lors de la configuration de l'année scolaire."




# ====================================================================
# 👥 GESTION PARENTS ET ÉLÈVES
# ====================================================================

def bulletins_accessible_pour_parent(eleve_id: int = None) -> bool:
    """
    Vérifie si les bulletins sont accessibles pour un parent.
    
    Args:
        eleve_id: ID de l'élève (optionnel)
    
    Returns:
        bool: True si bulletins accessibles
    """
    from app.models import PeriodeBulletin
    
    if not current_user.is_authenticated or current_user.role != 'parent':
        return False
    
    ecole_id = current_user.ecole_id
    if not ecole_id:
        return False
    
    # Vérifier qu'une période est publiée
    periode_publiee = PeriodeBulletin.query.filter_by(
        ecole_id=ecole_id,
        publie=True
    ).first()
    
    if not periode_publiee:
        return False
    
    # Si élève spécifié, vérifier l'accès via la fonction centralisée
    if eleve_id:
        from app.authorization import check_parent_access
        return check_parent_access(eleve_id)
    
    return True




# ====================================================================
# 🧾 LOGGING ET TRAÇABILITÉ
# ====================================================================

def log_utils_action(action: str, description: str, niveau: str = "info"):
    """
    Journalise une action administrative ou de correction.
    Version spécifique pour utils.py utilisant le middleware central.

    Args:
        action: Type d'action (ex: 'suppression', 'modification', 'export')
        description: Description textuelle
        niveau: 'info', 'warning', 'error'
    """
    try:
        from app.middleware import log_action
        
        log_action(
            module="utils",
            action=action,
            level=niveau.upper(),
            details=description
        )
        
        # Log dans la console Flask aussi
        logger = getattr(current_app.logger, niveau.lower(), current_app.logger.info)
        logger(f"[{action.upper()}] {description}")

    except Exception as e:
        current_app.logger.error(f"Erreur log_utils_action: {e}\n{traceback.format_exc()}")



# ====================================================================
# 📂 VALIDATION FICHIERS
# ====================================================================

def allowed_file(filename, allowed_extensions=None) -> bool:
    """
    Vérifie si un fichier a une extension autorisée.
    
    Args:
        filename (str): Nom du fichier à vérifier.
        allowed_extensions (set, optional): Extensions autorisées.
    
    Returns:
        bool: True si autorisé, False sinon.
    """
    if not filename or '.' not in filename:
        return False

    ext = filename.rsplit('.', 1)[1].lower()
    if allowed_extensions is None:
        allowed_extensions = {'txt', 'csv', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'xlsx', 'xls', 'doc', 'docx'}

    return ext in allowed_extensions


# ====================================================================
# 🔤 VALIDATION PARAMÈTRES DE TRI
# ====================================================================

def validate_sort_param(sort_param: str, allowed_fields: list[str], default: str = "id") -> str:
    """
    Valide un paramètre de tri provenant d'une requête (ex: sort=nom).
    
    Args:
        sort_param (str): Le paramètre reçu de la requête (ex: "nom" ou "-nom")
        allowed_fields (list[str]): Liste des champs autorisés pour le tri
        default (str): Champ par défaut si tri invalide

    Returns:
        str: Champ de tri validé (avec signe '-' s'il était demandé)
    """
    if not sort_param:
        return default

    # retirer les espaces
    sort_param = sort_param.strip()

    # vérifier si c'est un tri descendant
    descending = sort_param.startswith('-')
    field = sort_param[1:] if descending else sort_param

    # valider le champ
    if field not in allowed_fields:
        current_app.logger.warning(f"Paramètre de tri invalide: {sort_param}")
        return default

    return f"-{field}" if descending else field




# ====================================================================
# 🎯 FONCTIONS SUPPRIMÉES (DÉPLACÉES VERS AUTHORIZATION/MIDDLEWARE)
# ====================================================================

# ❌ SUPPRIMÉ : get_ecole_id() → Utiliser app.middleware.get_ecole_id()
# ❌ SUPPRIMÉ : check_parent_access() → Utiliser app.authorization.check_parent_access()
# ❌ SUPPRIMÉ : role_required() → Utiliser app.authorization.role_required()
# ❌ SUPPRIMÉ : get_objet_securise() → Utiliser app.middleware.check_ecole_access()
# ❌ SUPPRIMÉ : filtre_par_ecole() → Utiliser app.middleware.filtre_par_ecole()
# ❌ SUPPRIMÉ : log_correction() → Remplacé par log_utils_action()


# ====================================================================
# 🔗 IMPORT DES FONCTIONS CENTRALISÉES
# ====================================================================

# Import des fonctions du middleware pour compatibilité
from app.middleware import get_ecole_id, filtre_par_ecole, check_ecole_access
from app.services.annees_scolaires import get_annee_consultee



from app.models import Log, db
def log_action(user_id, action, details=None):
    """Enregistre une action utilisateur dans les logs"""
    try:
        log = Log(
            utilisateur_id=user_id,
            level="INFO",
            module="utils",
            action=action,
            details=details,
            timestamp=datetime.utcnow()
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"[log_action] Erreur : {e}")


# ====================================================================
# 🖼️ VALIDATION SÉCURISÉE DU LOGO ÉCOLE
# ====================================================================

# Mapping Pillow format string -> extension canonique sur disque
_LOGO_ALLOWED_FORMATS = {
    'PNG': 'png',
    'JPEG': 'jpg',
    'WEBP': 'webp',
}

# Taille maximale du logo en octets (2 Mo)
_LOGO_MAX_SIZE = 2 * 1024 * 1024


def validate_and_save_school_logo(file_storage, ecole, static_folder):
    """Valide le contenu binaire d'un logo uploadé et le sauvegarde de
    manière sécurisée.

    Retourne ``(True, None)`` en cas de succès, ou ``(False, message_erreur)``
    en cas d'échec.  Aucun fichier n'est écrit sur le disque en cas d'erreur.

    Sécurité :
    - Le contenu est inspecté avec ``PIL.Image`` (pas seulement l'extension).
    - Seuls PNG, JPEG et WEBP sont acceptés (SVG interdit).
    - Le nom de fichier d'origine est ignoré ; un UUID est généré.
    - L'ancien logo physique est supprimé si un remplacement réussit.
    """
    import os
    import uuid
    from PIL import Image, UnidentifiedImageError

    if not file_storage or not file_storage.filename:
        return False, "Aucun fichier sélectionné."

    # Lecture du flux en mémoire pour éviter d'écrire avant validation
    file_storage.seek(0, 2)  # seek end
    size = file_storage.tell()
    file_storage.seek(0)

    if size == 0:
        return False, "Le fichier est vide."

    if size > _LOGO_MAX_SIZE:
        return False, "Le fichier dépasse la taille maximale autorisée (2 Mo)."

    # Inspection du contenu réel avec Pillow
    try:
        img = Image.open(file_storage)
        img.verify()  # valide les en-têtes sans charger les pixels
    except (UnidentifiedImageError, Exception):
        return False, "Le fichier n'est pas une image valide (PNG, JPEG ou WEBP requis)."

    pil_format = img.format  # disponible après verify() sur l'objet initial
    if pil_format not in _LOGO_ALLOWED_FORMATS:
        return False, (
            f"Format « {pil_format or 'inconnu'} » non autorisé. "
            "Seuls PNG, JPEG et WEBP sont acceptés."
        )

    ext = _LOGO_ALLOWED_FORMATS[pil_format]

    # Générer un nom aléatoire sécurisé
    safe_name = f"logo_{uuid.uuid4().hex[:16]}.{ext}"

    school_dir = os.path.join(static_folder, 'ecoles', str(ecole.id))
    os.makedirs(school_dir, exist_ok=True)
    save_path = os.path.join(school_dir, safe_name)

    # Sauvegarder le nouveau fichier (relire depuis le début)
    file_storage.seek(0)
    file_storage.save(save_path)

    # Nettoyage de l'ancien logo physique
    old_logo = getattr(ecole, 'logo', None)
    if old_logo and old_logo != 'default_logo.png':
        old_path = os.path.join(school_dir, old_logo)
        if os.path.isfile(old_path) and os.path.abspath(old_path) != os.path.abspath(save_path):
            try:
                os.remove(old_path)
            except OSError:
                pass  # nettoyage best-effort, pas bloquant

    # Mettre à jour le modèle
    rel_path = f"ecoles/{ecole.id}/{safe_name}"
    ecole.logo_path = rel_path
    ecole.logo = safe_name

    return True, None


def nettoyer_repertoire_ecole(ecole_id: int, static_folder: Optional[str] = None) -> bool:
    """Supprime physiquement et de façon sécurisée le dossier d'uploads/fichiers

    d'une école (`app/static/ecoles/<ecole_id>/`) lors de sa suppression définitive.

    Garde-fous de sécurité :
    1. Validation stricte du type et de la positivité de `ecole_id`.
    2. Résolution des chemins absolus via `os.path.abspath`.
    3. Protection anti-path-traversal : vérifie que la cible est bien un sous-dossier
       strict de `dossier_base` (`static/ecoles/`) et n'est pas le dossier racine lui-même.
    4. Suppression récursive tolérante aux erreurs via `shutil.rmtree` sans bloquer
       les transactions de base de données.
    """
    import os
    import shutil

    if not isinstance(ecole_id, int) or ecole_id <= 0:
        current_app.logger.warning(
            f"[nettoyer_repertoire_ecole] ecole_id invalide : {ecole_id}"
        )
        return False

    base_dir = static_folder or getattr(current_app, 'static_folder', None)
    if not base_dir:
        base_dir = os.path.join(current_app.root_path, 'static')

    dossier_base = os.path.abspath(os.path.join(base_dir, 'ecoles'))
    dossier_cible = os.path.abspath(os.path.join(dossier_base, str(ecole_id)))

    # Vérification anti-traversal stricte
    if not dossier_cible.startswith(dossier_base + os.sep) or dossier_cible == dossier_base:
        current_app.logger.error(
            f"[nettoyer_repertoire_ecole] Détection tentative path-traversal ou cible interdite : {dossier_cible}"
        )
        return False

    if not os.path.exists(dossier_cible):
        current_app.logger.info(
            f"[nettoyer_repertoire_ecole] Dossier inexistant pour l'école {ecole_id}, rien à supprimer : {dossier_cible}"
        )
        return True

    try:
        shutil.rmtree(dossier_cible, ignore_errors=False)
        current_app.logger.info(
            f"[nettoyer_repertoire_ecole] Dossier supprimé avec succès pour l'école {ecole_id} : {dossier_cible}"
        )
        return True
    except Exception as e:
        current_app.logger.error(
            f"[nettoyer_repertoire_ecole] Erreur suppression dossier école {ecole_id} ({dossier_cible}): {e}"
        )
        # Ne pas lever d'exception pour préserver la transaction en DB
        return False
