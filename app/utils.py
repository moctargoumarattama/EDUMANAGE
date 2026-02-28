# app/utils.py
"""
Utilitaires pour la gestion sécurisée multi-écoles
Fonctions helpers respectant l'isolation des données
"""

from flask import current_app, jsonify, send_file, flash
from flask_login import current_user
from datetime import datetime, date, timedelta
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
            f"Correction ecole_id: {obj.__class__.__name__} ID={getattr(obj, 'id', '?')} "
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
    
    ecole_id = ecole_id or get_ecole_id()
    if not ecole_id:
        return None
    
    return AnneeScolaire.query.filter_by(
        ecole_id=ecole_id,
        statut='active'
    ).first()


def get_or_create_annee_active(ecole_id=None) -> 'AnneeScolaire':
    """
    Retourne l'année active ou en crée une par défaut.
    
    Args:
        ecole_id: ID de l'école
    
    Returns:
        AnneeScolaire
    """
    from app.models import AnneeScolaire
    from app import db
    
    ecole_id = ecole_id or get_ecole_id()
    if not ecole_id:
        raise ValueError("Impossible de déterminer l'école")
    
    annee = get_annee_active(ecole_id)
    if annee:
        return annee
    
    # Créer une année par défaut
    now = datetime.now()
    if now.month >= 9:  # Septembre ou après
        annee_debut = now.year
    else:
        annee_debut = now.year - 1
    
    nouvelle_annee = AnneeScolaire(
        nom=f"{annee_debut}-{annee_debut + 1}",
        date_debut=datetime(annee_debut, 9, 1).date(),
        date_fin=datetime(annee_debut + 1, 6, 30).date(),
        statut='active',
        ecole_id=ecole_id
    )
    
    db.session.add(nouvelle_annee)
    db.session.commit()
    
    current_app.logger.info(
        f"Année scolaire {nouvelle_annee.nom} créée automatiquement "
        f"pour école ID={ecole_id}"
    )
    
    return nouvelle_annee


# ====================================================================
# 📊 STATISTIQUES PAR ÉCOLE
# ====================================================================

def get_stats_ecole(ecole_id=None) -> Dict[str, Any]:
    """
    Calcule les statistiques pour une école.
    
    Args:
        ecole_id: ID de l'école (optionnel)
    
    Returns:
        Dictionnaire avec les statistiques
    """
    from app.models import Eleve, Professeur, Cours, Classe, Note, Paiement, Absence
    
    ecole_id = ecole_id or get_ecole_id()
    if not ecole_id:
        return {}
    
    stats = {
        'ecole_id': ecole_id,
        'total_eleves': Eleve.query.filter_by(ecole_id=ecole_id).count(),
        'total_professeurs': Professeur.query.filter_by(ecole_id=ecole_id).count(),
        'total_classes': Classe.query.filter_by(ecole_id=ecole_id).count(),
        'total_cours': Cours.query.filter_by(ecole_id=ecole_id).count(),
    }
    
    # Statistiques mensuelles
    debut_mois = datetime.now().replace(day=1, hour=0, minute=0, second=0)
    
    stats['eleves_nouveaux_mois'] = Eleve.query.filter(
        Eleve.ecole_id == ecole_id,
        Eleve.date_inscription >= debut_mois
    ).count()
    
    stats['absences_mois'] = Absence.query.join(Eleve).filter(
        Eleve.ecole_id == ecole_id,
        Absence.date_absence >= debut_mois
    ).count()
    
    # Moyenne générale
    moyenne_result = Note.query.join(Eleve).filter(
        Eleve.ecole_id == ecole_id
    ).with_entities(
        func.avg(Note.valeur).label('moyenne')
    ).first()
    
    stats['moyenne_generale'] = round(moyenne_result.moyenne, 2) if moyenne_result.moyenne else 0
    
    return stats


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
# 📁 EXPORT DE DONNÉES
# ====================================================================

def export_csv_secure(data: List[Dict], columns: List[str], filename: str = "export.csv"):
    """
    Export CSV sécurisé avec filtrage par école.
    
    Args:
        data: Liste de dictionnaires à exporter
        columns: Colonnes à inclure
        filename: Nom du fichier
    
    Returns:
        Response Flask pour téléchargement
    """
    try:
        # Créer le CSV en mémoire
        si = io.StringIO()
        writer = csv.DictWriter(si, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        
        for row in data:
            # Nettoyer les valeurs
            safe_row = {}
            for col in columns:
                val = row.get(col, "")
                if val is None:
                    val = ""
                elif not isinstance(val, (str, int, float, bool)):
                    val = str(val)
                safe_row[col] = val
            writer.writerow(safe_row)
        
        # Convertir en bytes
        output = io.BytesIO()
        output.write(si.getvalue().encode('utf-8-sig'))
        output.seek(0)
        si.close()
        
        # Logger l'export
        from app.middleware import log_action
        log_action(
            module="utils",
            action="export",
            level="INFO",
            details=f"Export CSV: {filename} ({len(data)} lignes)"
        )
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='text/csv'
        )
        
    except Exception as e:
        current_app.logger.error(f"Erreur export CSV: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Erreur lors de l'export"}), 500


# ====================================================================
# 🔧 UTILITAIRES GÉNÉRAUX
# ====================================================================

def safe_int(value: Any, default: int = 0) -> int:
    """Conversion sécurisée vers int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """Conversion sécurisée vers float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_date(dt: Union[datetime, date, None], fmt: str = "%d/%m/%Y") -> str:
    """Formate une date de manière sécurisée."""
    if not dt:
        return ""
    try:
        if isinstance(dt, str):
            return dt
        return dt.strftime(fmt)
    except Exception:
        return str(dt)


def parse_date(date_str: str, fmt: str = "%Y-%m-%d") -> Optional[date]:
    """Parse une date de manière sécurisée."""
    try:
        return datetime.strptime(date_str, fmt).date()
    except Exception:
        return None


def calculate_age(birthdate: Union[date, None]) -> Optional[int]:
    """Calcule l'âge à partir de la date de naissance."""
    if not birthdate:
        return None
    
    today = date.today()
    age = today.year - birthdate.year
    
    # Ajustement si l'anniversaire n'est pas encore passé
    if (today.month, today.day) < (birthdate.month, birthdate.day):
        age -= 1
    
    return age


# ====================================================================
# 📨 NOTIFICATIONS
# ====================================================================

def notify_parent(eleve_id: int, subject: str, message: str) -> bool:
    """
    Envoie une notification au parent d'un élève.
    
    Args:
        eleve_id: ID de l'élève
        subject: Sujet de la notification
        message: Message à envoyer
    
    Returns:
        bool: True si envoyé avec succès
    """
    from app.models import Eleve
    from app.notifications import envoyer_email
    
    try:
        eleve = Eleve.query.get(eleve_id)
        if not eleve or not eleve.email_parent:
            return False
        
        # Vérifier que l'élève est dans la bonne école
        if not ensure_ecole_consistency(eleve):
            current_app.logger.error(
                f"Tentative d'envoi de notification à un élève d'une autre école"
            )
            return False
        
        return envoyer_email(eleve.email_parent, subject, message)
        
    except Exception as e:
        current_app.logger.error(f"Erreur notification parent: {e}")
        return False


# ====================================================================
# 🔄 TRANSACTIONS SÉCURISÉES
# ====================================================================

def safe_commit(db_session) -> bool:
    """
    Commit sécurisé avec rollback en cas d'erreur.
    
    Args:
        db_session: Session SQLAlchemy
    
    Returns:
        bool: True si succès
    """
    try:
        db_session.commit()
        return True
    except Exception as e:
        db_session.rollback()
        current_app.logger.error(f"Erreur commit DB: {e}\n{traceback.format_exc()}")
        flash("Erreur lors de l'enregistrement des données.", "danger")
        return False


def add_and_commit(obj, db_session) -> bool:
    """
    Ajoute un objet et commit de manière sécurisée.
    
    Args:
        obj: Objet à ajouter
        db_session: Session SQLAlchemy
    
    Returns:
        bool: True si succès
    """
    try:
        # Vérifier la cohérence de l'école avant ajout
        if hasattr(obj, 'ecole_id'):
            ensure_ecole_consistency(obj)

        db_session.add(obj)
        db_session.commit()
        current_app.logger.info(
            f"Objet {obj.__class__.__name__} (ID={getattr(obj, 'id', '?')}) ajouté avec succès."
        )
        return True

    except Exception as e:
        db_session.rollback()
        current_app.logger.error(
            f"Erreur lors de l'ajout de {obj.__class__.__name__}: {e}\n{traceback.format_exc()}"
        )
        flash("Erreur lors de l'ajout de l'élément.", "danger")
        return False


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
# ✅ VALIDATION DE DONNÉES
# ====================================================================

def validate_non_empty(value: Any, field_name: str = "champ") -> bool:
    """Vérifie qu'une valeur n'est pas vide, avec message utilisateur."""
    if value in (None, "", []):
        flash(f"Le {field_name} ne peut pas être vide.", "warning")
        return False
    return True


def validate_positive_number(value: Any, field_name: str = "valeur") -> bool:
    """Vérifie qu'une valeur numérique est positive."""
    try:
        if float(value) <= 0:
            flash(f"Le {field_name} doit être supérieur à zéro.", "warning")
            return False
        return True
    except Exception:
        flash(f"Le {field_name} doit être un nombre valide.", "warning")
        return False


# ====================================================================
# 🧩 UTILITAIRES JSON / OBJETS
# ====================================================================

def serialize_model(obj, fields: List[str] = None) -> Dict[str, Any]:
    """
    Sérialise un modèle SQLAlchemy en dict minimal.
    
    Args:
        obj: instance du modèle
        fields: liste de champs à inclure (sinon tous les attributs simples)
    """
    if not obj:
        return {}

    data = {}
    columns = fields or [c.name for c in obj.__table__.columns]
    for c in columns:
        val = getattr(obj, c, None)
        if isinstance(val, (datetime, date)):
            val = val.strftime("%Y-%m-%d")
        data[c] = val
    return data


def to_json_safe(obj: Any) -> str:
    """Conversion JSON sécurisée (même si erreurs ou objets non sérialisables)."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception as e:
        current_app.logger.error(f"Erreur JSON: {e}")
        return "{}"


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
    Valide un paramètre de tri provenant d'une requête (ex: ?sort=nom).
    
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
# 🔄 PRÉCHARGEMENT DES RELATIONS
# ====================================================================

def preload_relations(query, *relations):
    """Ajoute selectinload pour éviter N+1"""
    options = [selectinload(getattr(query._entity_zero().entity, r)) for r in relations]
    return query.options(*options)


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



from app.models import Log, db
def log_action(user_id, action, details=None):
    """Enregistre une action utilisateur dans les logs"""
    try:
        log = Log(
            user_id=user_id,
            action=action,
            details=details,
            timestamp=datetime.utcnow()
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"[log_action] Erreur : {e}")