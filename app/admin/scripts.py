from flask import send_file, current_app
from app.utils import get_ecole_filter_query
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, date, time
from app import db
from app.models import Log, ParametreSysteme
import json
from app.models import Note, Absence, Ecole, Classe, Eleve, Professeur, Utilisateur, AnneeScolaire


# --- Configuration ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
DB_PATH = os.path.join(BASE_DIR, "instance", "ecole.db")
DEPLOY_SCRIPT = 'scripts/deploy.sh'
os.makedirs('scripts', exist_ok=True)

# --- Initialisation des années scolaires ---
def init_annees_scolaires():
    """Initialise les années scolaires par défaut"""
    try:
        annees = [
            {'nom': '2023-2024', 'debut': '2023-09-01', 'fin': '2024-07-31'},
            {'nom': '2024-2025', 'debut': '2024-09-01', 'fin': '2025-07-31'},
            {'nom': '2025-2026', 'debut': '2025-09-01', 'fin': '2026-07-31'},
        ]
        
        for annee in annees:
            if not AnneeScolaire.query.filter_by(nom=annee['nom']).first():
                new_annee = AnneeScolaire(
                    nom=annee['nom'],
                    date_debut=datetime.strptime(annee['debut'], '%Y-%m-%d').date(),
                    date_fin=datetime.strptime(annee['fin'], '%Y-%m-%d').date()
                )
                db.session.add(new_annee)
        
        db.session.commit()
        log_action("INITIALISATION", "Années scolaires initialisées")
        return "Années scolaires initialisées"
    except Exception as e:
        log_action("ERREUR", f"Erreur initialisation années: {str(e)}", level="ERROR")
        return f"Erreur: {str(e)}"

# --- Création des tables manquantes ---
def create_missing_tables():
    """Crée les tables manquantes essentielles"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Créer la table annee_scolaire si elle n'existe pas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS annee_scolaire (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom VARCHAR(20) UNIQUE NOT NULL,
                date_debut DATE NOT NULL,
                date_fin DATE NOT NULL,
                statut VARCHAR(20) DEFAULT 'active'
            )
        """)
        
        # Créer la table session si elle n'existe pas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session (
                id VARCHAR(255) PRIMARY KEY,
                data TEXT,
                expiration DATETIME
            )
        """)
        
        # Créer la table log si elle n'existe pas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                level VARCHAR(20),
                module VARCHAR(100),
                action TEXT,
                details TEXT,
                utilisateur_id INTEGER,
                ip_address VARCHAR(45)
            )
        """)
        
        # Ajouter la colonne annee_scolaire_id à la table classe si elle n'existe pas
        cursor.execute("PRAGMA table_info(classe)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'annee_scolaire_id' not in columns:
            cursor.execute("ALTER TABLE classe ADD COLUMN annee_scolaire_id INTEGER DEFAULT 1")
        
        conn.commit()
        current_app.logger.info("Tables manquantes créées avec succès")
        
        # Initialiser les années scolaires après création des tables
        init_annees_scolaires()
        
        return True
        
    except Exception as e:
        current_app.logger.error(f"Erreur création tables: {str(e)}")
        return False
    finally:
        conn.close()

# --- Script de déploiement ---
def create_deploy_script():
    script_content = """#!/bin/bash
# Script de déploiement pour KLASORA
echo "Début du déploiement à $(date)"

# Mise à jour du code
echo "Mise à jour du code depuis Git..."
git pull origin main

# Installation des dépendances
echo "Installation des dépendances..."
pip install -r requirements.txt

# Migration de la base de données
echo "Migration de la base de données..."
flask db upgrade

# Redémarrage du service
echo "Redémarrage du service..."
sudo systemctl restart edumanage

echo "Déploiement terminé à $(date)"
"""

    with open(DEPLOY_SCRIPT, 'w') as f:
        f.write(script_content)
    
    os.chmod(DEPLOY_SCRIPT, 0o755)
    return DEPLOY_SCRIPT

# --- Sauvegarde ---
def create_backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
    shutil.copy2(DB_PATH, backup_file)
    
    log_action("SAUVEGARDE", f"Sauvegarde créée: {backup_file}")
    optimize_database()
    
    return backup_file

# --- Restauration ---
def restore_backup(filename):
    backup_file = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(backup_file):
        current_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        recovery_file = os.path.join(BACKUP_DIR, f"recovery_{current_timestamp}.db")
        shutil.copy2(DB_PATH, recovery_file)

        shutil.copy2(backup_file, DB_PATH)
        log_action("RESTAURATION", f"Restauration depuis: {filename}")
        return True
    else:
        raise Exception("Fichier de sauvegarde introuvable!")

# --- Nettoyage (CORRIGÉ) ---
def clean_data():
    """Nettoyage des données avec gestion des tables manquantes"""
    log_action("NETTOYAGE", "Début du nettoyage des données")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Nettoyer les logs de plus d'un an (si la table existe)
        try:
            cursor.execute("DELETE FROM log WHERE timestamp < datetime('now', '-1 year')")
            deleted_logs = cursor.rowcount
        except sqlite3.OperationalError:
            deleted_logs = 0  # Table log n'existe pas encore

        # Nettoyer les données temporaires (si la table session existe)
        try:
            cursor.execute("DELETE FROM session WHERE expiration < datetime('now')")
            deleted_sessions = cursor.rowcount
        except sqlite3.OperationalError:
            deleted_sessions = 0  # Table session n'existe pas

        conn.commit()
        
        result = f"{deleted_logs} logs et {deleted_sessions} sessions nettoyés"
        log_action("NETTOYAGE", f"Nettoyage terminé: {result}")
        
    except Exception as e:
        result = f"Erreur lors du nettoyage: {str(e)}"
        current_app.logger.error(f"ERREUR NETTOYAGE: {result}")
    finally:
        conn.close()

    return result

# --- Optimisation de la base de données ---
def optimize_database():
    """Optimise la base de données SQLite"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("VACUUM")
        cursor.execute("PRAGMA optimize")
        conn.close()
        
        log_action("OPTIMISATION", "Base de données optimisée")
        return True
    except Exception as e:
        log_action("ERREUR", f"Erreur optimisation BD: {str(e)}", level="ERROR")
        return False

# --- Déploiement ---
def deploy_app():
    """Exécute le script de déploiement"""
    try:
        if not os.path.exists(DEPLOY_SCRIPT):
            create_deploy_script()
            
        result = subprocess.run([DEPLOY_SCRIPT], capture_output=True, text=True, shell=True)
        
        if result.returncode == 0:
            log_action("DEPLOIEMENT", "Déploiement réussi")
            return result.stdout
        else:
            error_msg = f"Erreur déploiement: {result.stderr}"
            log_action("ERREUR", error_msg, level="ERROR")
            return error_msg
            
    except Exception as e:
        error_msg = f"Exception lors du déploiement: {str(e)}"
        log_action("ERREUR", error_msg, level="ERROR")
        return error_msg

# --- Journalisation (CORRIGÉ) ---
def log_action(module, action, level="INFO", user_id=None, details=None):
    """Journalise une action avec gestion des verrouillages"""
    try:
        # Journaliser d'abord avec le logger système
        if level == "ERROR":
            current_app.logger.error(f"{module}: {action} - {details}")
        elif level == "WARNING":
            current_app.logger.warning(f"{module}: {action} - {details}")
        else:
            current_app.logger.info(f"{module}: {action} - {details}")
        
        # Ensuite, tenter d'écrire dans la table log (si elle existe)
        try:
            log_entry = Log(
                level=level,
                module=module,
                action=action,
                details=details,
                utilisateur_id=user_id,
                ip_address="127.0.0.1"
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as db_error:
            current_app.logger.warning(f"Impossible d'écrire dans la table log: {db_error}")
            
    except Exception as e:
        import logging
        logging.error(f"Erreur journalisation: {str(e)}")

# --- Statistiques système ---
def get_system_stats():
    stats = {}
    backups = [f for f in os.listdir(BACKUP_DIR) if f.startswith('backup_') and f.endswith('.db')]
    stats['last_backup'] = max(backups, key=lambda f: os.path.getctime(os.path.join(BACKUP_DIR, f))) if backups else None

    try:
        total, used, free = shutil.disk_usage("/")
        stats['disk_usage'] = round((used / total) * 100, 1)
    except OSError as e:
        current_app.logger.warning(f"Impossible de lire l'utilisation disque: {e}")
        stats['disk_usage'] = "N/A"

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT sqlite_version()")
        version = cursor.fetchone()
        stats['db_version'] = version[0] if version else "N/A"
        
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        stats['table_count'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        stats['tables'] = [table[0] for table in tables]
        
        conn.close()
    except sqlite3.Error as e:
        current_app.logger.warning(f"Impossible de lire les statistiques SQLite: {e}")
        stats['db_version'] = "N/A"
        stats['table_count'] = "N/A"
        stats['tables'] = []

    stats['app_version'] = current_app.config.get('VERSION', 'N/A')
    stats['log_count'] = Log.query.count() if hasattr(Log, 'query') else 0
    stats['log_recent'] = Log.query.filter(Log.timestamp >= datetime.now() - timedelta(days=7)).count() if hasattr(Log, 'query') else 0

    return stats

# --- Vérification d'intégrité ---
def integrity_check():
    """
    Vérifie l'intégrité de la base et corrige les colonnes manquantes critiques.
    """
    results = []
    summary = {"total": 0, "success": 0, "errors": 0}

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Vérification globale
        cursor.execute("PRAGMA integrity_check")
        row = cursor.fetchone()
        summary["total"] += 1
        if not row:
            results.append("Impossible de vérifier la base")
            summary["errors"] += 1
        elif row[0] != "ok":
            results.append(f"Problèmes détectés: {row[0]} ❌")
            summary["errors"] += 1
        else:
            results.append("Base de données intègre ✅")
            summary["success"] += 1

        # Vérification colonnes critiques
        critical_columns = {
            "utilisateur": ["dernier_acces", "statut"],
            "eleve": ["frais_annuels", "code_parent"],
            "note": ["coefficient", "type_evaluation"],
            "paiement": ["statut", "reference"]
        }

        for table, cols in critical_columns.items():
            cursor.execute(f"PRAGMA table_info({table})")
            existing_cols = [col[1] for col in cursor.fetchall()]
            summary["total"] += len(cols)

            for col in cols:
                if col not in existing_cols:
                    try:
                        if col in ["dernier_acces", "date_creation"]:
                            col_type = "TIMESTAMP"
                        elif col in ["frais_annuels", "coefficient"]:
                            col_type = "FLOAT DEFAULT 0"
                        elif col in ["statut"]:
                            col_type = "VARCHAR(20) DEFAULT 'actif'"
                        else:
                            col_type = "VARCHAR(100)"
                            
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                        conn.commit()
                        results.append(f"Colonne manquante ajoutée : {table}.{col} ✅")
                        summary["success"] += 1
                    except Exception as e:
                        results.append(f"Erreur lors de l'ajout de {table}.{col} : {str(e)} ❌")
                        summary["errors"] += 1
                else:
                    results.append(f"Colonne {table}.{col} OK ✅")
                    summary["success"] += 1

        conn.close()
        
        log_action("INTEGRITE", f"Vérification d'intégrité: {summary['success']} succès, {summary['errors']} erreurs")
        
    except Exception as e:
        results.append(f"Erreur lors de la vérification : {str(e)} ❌")
        summary["errors"] += 1
        log_action("ERREUR", f"Échec vérification intégrité: {str(e)}", level="ERROR")

    return results, summary

# --- Suppression / Téléchargement sauvegarde ---
def delete_backup_file(filename):
    """Supprime un fichier de sauvegarde"""
    backup_file = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(backup_file):
        os.remove(backup_file)
        log_action("SAUVEGARDE", f"Sauvegarde supprimée: {filename}")
        return True
    else:
        raise Exception("Fichier de sauvegarde introuvable!")

def download_backup_file(filename):
    """Télécharge un fichier de sauvegarde"""
    backup_file = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(backup_file):
        return send_file(backup_file, as_attachment=True, download_name=filename)
    else:
        raise Exception("Fichier de sauvegarde introuvable!")

# --- Sauvegardes par école ---
def create_complete_backup():
    """Sauvegarde complète de toutes les données"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"complete_backup_{timestamp}.json")
    
    backup_data = {
        'metadata': {
            'type': 'complete',
            'timestamp': timestamp,
            'version': '1.0',
            'app_version': current_app.config.get('VERSION', 'N/A')
        },
        'data': {}
    }
    
    try:
        ecoles = get_ecole_filter_query(Ecole).all()
        backup_data['data']['ecoles'] = [ecole.to_dict() for ecole in ecoles]
        
        # Ajouter les années scolaires
        annees_scolaires = get_ecole_filter_query(AnneeScolaire).all()
        backup_data['data']['annees_scolaires'] = [annee.to_dict() for annee in annees_scolaires]
        
        tables = [Classe, Eleve, Professeur, Note, Absence, Utilisateur]
        for table in tables:
            table_name = table.__tablename__
            items = get_ecole_filter_query(table).all()
            backup_data['data'][table_name] = [item.to_dict() for item in items]
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        log_action("SAUVEGARDE", f"Sauvegarde complète créée: {backup_file}")
        return backup_file
        
    except Exception as e:
        log_action("ERREUR", f"Erreur sauvegarde complète: {str(e)}", level="ERROR")
        raise e

def _serialize_instance(obj):
    if not obj:
        return None
    d = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name)
        if isinstance(val, (datetime, date, time)):
            val = val.isoformat()
        d[col.name] = val
    return d


def _compute_backup_checksum(data_dict):
    import hashlib
    raw = json.dumps(data_dict, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _deserialize_row(model_class, row_dict):
    from sqlalchemy import Date, DateTime, Time
    kwargs = {}
    for col in model_class.__table__.columns:
        name = col.name
        if name in row_dict:
            val = row_dict[name]
            if val is not None:
                if isinstance(col.type, Time):
                    if isinstance(val, str):
                        try:
                            val = time.fromisoformat(val)
                        except ValueError:
                            pass
                elif isinstance(col.type, (Date, DateTime)):
                    if isinstance(val, str):
                        try:
                            if 'T' in val or ' ' in val:
                                val = datetime.fromisoformat(val)
                            else:
                                val = date.fromisoformat(val)
                        except ValueError:
                            pass
            kwargs[name] = val
    return model_class(**kwargs)


def cleanup_old_automatic_backups(ecole_id, keep=3):
    """
    Conserve les `keep` plus récentes sauvegardes automatiques d'une école
    et supprime les plus anciennes sauvegardes automatiques de cette même école.
    Ne touche PAS aux sauvegardes manuelles, safety_restore ou aux autres écoles.
    """
    auto_backups = []
    if not os.path.exists(BACKUP_DIR):
        return 0

    for file in os.listdir(BACKUP_DIR):
        if file.startswith(f"school_{ecole_id}_") and file.endswith(".json"):
            file_path = os.path.join(BACKUP_DIR, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f).get("metadata", {})
                if metadata.get("ecole_id") == ecole_id and metadata.get("backup_type") == "automatic":
                    created_at_raw = metadata.get("created_at") or metadata.get("timestamp")
                    try:
                        ts = datetime.fromisoformat(created_at_raw)
                    except (ValueError, TypeError):
                        ts = datetime.fromtimestamp(os.path.getmtime(file_path))
                    auto_backups.append({
                        "file": file,
                        "file_path": file_path,
                        "created_at": ts
                    })
            except Exception:
                continue

    auto_backups.sort(key=lambda x: x["created_at"], reverse=True)

    deleted_count = 0
    if len(auto_backups) > keep:
        for b in auto_backups[keep:]:
            try:
                os.remove(b["file_path"])
                deleted_count += 1
                log_action("BACKUP_AUTO_CLEANUP", f"Ancienne sauvegarde automatique supprimée pour l'école ID={ecole_id}: {b['file']}")
            except OSError as e:
                current_app.logger.warning(f"Impossible de supprimer {b['file_path']}: {e}")

    return deleted_count


def run_daily_automatic_backups():
    """
    Exécute la sauvegarde automatique quotidienne pour toutes les écoles
    (y compris suspendues et en maintenance).
    Exécution isolée par école : l'échec d'une école ne bloque pas les autres.
    """
    from app.models import Ecole
    ecoles = Ecole.query.all()
    results = {'success': 0, 'failed': 0, 'skipped': 0, 'details': []}

    log_action("BACKUP_AUTO_START", f"Début de la sauvegarde automatique quotidienne pour {len(ecoles)} école(s).")

    for ecole in ecoles:
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            already_done = False
            for file in os.listdir(BACKUP_DIR):
                if file.startswith(f"school_{ecole.id}_") and file.endswith(".json"):
                    fpath = os.path.join(BACKUP_DIR, file)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            meta = json.load(f).get('metadata', {})
                        if (meta.get('ecole_id') == ecole.id and
                            meta.get('backup_type') == 'automatic' and
                            str(meta.get('created_at', '')).startswith(today_str)):
                            already_done = True
                            break
                    except Exception:
                        continue

            if already_done:
                results['skipped'] += 1
                results['details'].append({'ecole_id': ecole.id, 'ecole_nom': ecole.nom, 'status': 'skipped'})
                log_action("BACKUP_AUTO_SKIPPED", f"Sauvegarde automatique déjà effectuée aujourd'hui pour l'école '{ecole.nom}' (ID={ecole.id})")
                continue

            backup_file = create_school_backup(ecole.id, backup_type="automatic")
            results['success'] += 1
            results['details'].append({'ecole_id': ecole.id, 'ecole_nom': ecole.nom, 'status': 'success', 'file': os.path.basename(backup_file)})
            log_action("BACKUP_AUTO_SUCCESS", f"Sauvegarde automatique réussie pour l'école '{ecole.nom}' (ID={ecole.id})")
        except Exception as e:
            results['failed'] += 1
            results['details'].append({'ecole_id': ecole.id, 'ecole_nom': ecole.nom, 'status': 'failed', 'error': str(e)})
            log_action("BACKUP_AUTO_FAILED", f"Échec sauvegarde automatique pour l'école '{ecole.nom}' (ID={ecole.id}): {e}", level="ERROR")

    log_action("BACKUP_AUTO_END", f"Sauvegarde automatique terminée: {results['success']} réussie(s), {results['skipped']} ignorée(s), {results['failed']} échouée(s).")
    return results


def create_school_backup(ecole_id, backup_type="manual"):
    """Sauvegarde complète et sécurisée des données d'une école spécifique"""
    from app.models import (
        Ecole, Utilisateur, Professeur, AnneeScolaire, AnneeNiveauConfig,
        EcoleNiveauConfig, Classe, Eleve, Inscription, Cours, Note, Absence,
        Paiement, Bulletin, EmploiTemps, PeriodeBulletin, Presence, Alerte,
        ArchiveNote, ArchiveAbsence, EcoleGoogleMailConfig, JournalCorrection,
        SyncOperationLog, SupportTicket, HistoriqueImport, professeur_classes
    )

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Si sauvegarde automatique, vérifier l'idempotence quotidienne pour cette école
    if backup_type == "automatic" and os.path.exists(BACKUP_DIR):
        for file in os.listdir(BACKUP_DIR):
            if file.startswith(f"school_{ecole_id}_") and file.endswith(".json"):
                fpath = os.path.join(BACKUP_DIR, file)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        meta = json.load(f).get('metadata', {})
                    if (meta.get('ecole_id') == ecole_id and
                        meta.get('backup_type') == 'automatic' and
                        str(meta.get('created_at', '')).startswith(today_str)):
                        log_action("BACKUP_AUTO_SKIP", f"Sauvegarde automatique déjà existante aujourd'hui pour l'école ID={ecole_id}")
                        return fpath
                except Exception:
                    continue

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ecole = Ecole.query.get(ecole_id)
    if not ecole:
        raise ValueError("École non trouvée")

    user_ids_subq = db.session.query(Utilisateur.id).filter_by(ecole_id=ecole_id)
    eleve_ids_subq = db.session.query(Eleve.id).filter_by(ecole_id=ecole_id)

    prof_classes_raw = db.session.execute(
        professeur_classes.select().where(professeur_classes.c.ecole_id == ecole_id)
    ).all()
    prof_classes_data = []
    for r in prof_classes_raw:
        row_dict = dict(r._mapping) if hasattr(r, '_mapping') else dict(r)
        if isinstance(row_dict.get('date_assignation'), (datetime, date, time)):
            row_dict['date_assignation'] = row_dict['date_assignation'].isoformat()
        prof_classes_data.append(row_dict)

    data = {
        'ecole': _serialize_instance(ecole),
        'ecole_niveau_configs': [_serialize_instance(c) for c in EcoleNiveauConfig.query.filter_by(ecole_id=ecole_id).all()],
        'utilisateurs': [_serialize_instance(u) for u in Utilisateur.query.filter_by(ecole_id=ecole_id).filter(Utilisateur.role != 'super_admin').all()],
        'professeurs': [_serialize_instance(p) for p in Professeur.query.filter_by(ecole_id=ecole_id).all()],
        'annees_scolaires': [_serialize_instance(a) for a in AnneeScolaire.query.filter_by(ecole_id=ecole_id).all()],
        'annee_niveau_configs': [_serialize_instance(c) for c in AnneeNiveauConfig.query.filter_by(ecole_id=ecole_id).all()],
        'periodes_bulletin': [_serialize_instance(pb) for pb in PeriodeBulletin.query.filter_by(ecole_id=ecole_id).all()],
        'classes': [_serialize_instance(c) for c in Classe.query.filter_by(ecole_id=ecole_id).all()],
        'eleves': [_serialize_instance(e) for e in Eleve.query.filter_by(ecole_id=ecole_id).all()],
        'inscriptions': [_serialize_instance(i) for i in Inscription.query.filter_by(ecole_id=ecole_id).all()],
        'cours': [_serialize_instance(c) for c in Cours.query.filter_by(ecole_id=ecole_id).all()],
        'emplois_temps': [_serialize_instance(et) for et in EmploiTemps.query.filter((EmploiTemps.ecole_id == ecole_id) | (EmploiTemps.classe_id.in_(db.session.query(Classe.id).filter_by(ecole_id=ecole_id)))).all()],
        'professeur_classes': prof_classes_data,
        'notes': [_serialize_instance(n) for n in Note.query.filter_by(ecole_id=ecole_id).all()],
        'absences': [_serialize_instance(a) for a in Absence.query.filter_by(ecole_id=ecole_id).all()],
        'presences': [_serialize_instance(p) for p in Presence.query.join(Eleve).filter(Eleve.ecole_id == ecole_id).all()],
        'paiements': [_serialize_instance(p) for p in Paiement.query.filter_by(ecole_id=ecole_id).all()],
        'bulletins': [_serialize_instance(b) for b in Bulletin.query.filter_by(ecole_id=ecole_id).all()],
        'archive_notes': [_serialize_instance(an) for an in ArchiveNote.query.filter(ArchiveNote.eleve_id.in_(eleve_ids_subq)).all()],
        'archive_absences': [_serialize_instance(aa) for aa in ArchiveAbsence.query.filter(ArchiveAbsence.eleve_id.in_(eleve_ids_subq)).all()],
        'alertes': [_serialize_instance(a) for a in Alerte.query.filter((Alerte.eleve_id.in_(eleve_ids_subq)) | (Alerte.utilisateur_id.in_(user_ids_subq))).all()],
        'ecole_google_mail_configs': [_serialize_instance(g) for g in EcoleGoogleMailConfig.query.filter_by(ecole_id=ecole_id).all()],
        'journal_corrections': [_serialize_instance(j) for j in JournalCorrection.query.filter_by(ecole_id=ecole_id).all()],
        'sync_operation_logs': [_serialize_instance(s) for s in SyncOperationLog.query.filter_by(ecole_id=ecole_id).all()],
        'support_tickets': [_serialize_instance(st) for st in SupportTicket.query.filter_by(ecole_id=ecole_id).all()],
        'historique_imports': [_serialize_instance(hi) for hi in HistoriqueImport.query.filter(HistoriqueImport.utilisateur_id.in_(user_ids_subq)).all()],
    }

    counts = {k: len(v) if isinstance(v, list) else (1 if v else 0) for k, v in data.items()}
    checksum = _compute_backup_checksum(data)

    metadata = {
        'type': 'school',
        'backup_type': backup_type,
        'version': '2.0',
        'ecole_id': ecole.id,
        'ecole_nom': ecole.nom,
        'timestamp': timestamp,
        'created_at': datetime.now().isoformat(),
        'counts': counts,
        'checksum': checksum,
    }

    backup_data = {
        'metadata': metadata,
        'data': data,
    }

    backup_file = os.path.join(BACKUP_DIR, f"school_{ecole.id}_{timestamp}_{backup_type}.json")
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, indent=2, ensure_ascii=False)

    log_action("SAUVEGARDE", f"Sauvegarde ({backup_type}) école '{ecole.nom}' (ID={ecole.id}) créée avec succès: {os.path.basename(backup_file)}")

    if backup_type == "automatic":
        cleanup_old_automatic_backups(ecole_id, keep=3)

    return backup_file


def inspect_school_backup(filename):
    """Vérifie la validité, l'intégrité et lit les métadonnées d'une sauvegarde d'école."""
    backup_file = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(backup_file):
        raise ValueError("Fichier de sauvegarde introuvable")

    try:
        with open(backup_file, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
    except Exception as e:
        raise ValueError(f"Fichier de sauvegarde illisible ou invalide : {e}")

    metadata = backup_data.get('metadata', {})
    data = backup_data.get('data', {})

    if metadata.get('type') != 'school' or not data:
        raise ValueError("Type de sauvegarde invalide (sauvegarde d'école requise)")

    expected_checksum = metadata.get('checksum')
    if expected_checksum:
        actual_checksum = _compute_backup_checksum(data)
        if expected_checksum != actual_checksum:
            raise ValueError("RESTORE REFUSÉ : Fichier de sauvegarde corrompu ou altéré (checksum invalide).")

    return metadata, data


def restore_school_backup(filename, target_ecole_id=None, confirmation_code=None):
    """
    Restaure une école depuis sa sauvegarde avec contrôle d'intégrité,
    sauvegarde automatique de sécurité préalable et confirmation forte.
    """
    from app.models import (
        Ecole, Utilisateur, Professeur, AnneeScolaire, AnneeNiveauConfig,
        EcoleNiveauConfig, Classe, Eleve, Inscription, Cours, Note, Absence,
        Paiement, Bulletin, EmploiTemps, PeriodeBulletin, Presence, Alerte,
        ArchiveNote, ArchiveAbsence, EcoleGoogleMailConfig, JournalCorrection,
        SyncOperationLog, SupportTicket, HistoriqueImport, professeur_classes
    )
    metadata, data = inspect_school_backup(filename)
    ecole_id = metadata.get('ecole_id')
    ecole_nom = metadata.get('ecole_nom', '')

    if target_ecole_id and ecole_id != target_ecole_id:
        raise ValueError(f"RESTORE REFUSÉ : La sauvegarde appartient à l'école ID #{ecole_id} ('{ecole_nom}'), pas à l'école ID #{target_ecole_id}.")

    if confirmation_code is not None:
        code_valid = (
            str(confirmation_code).strip().upper() == "RESTAURER" or
            str(confirmation_code).strip().lower() == ecole_nom.lower()
        )
        if not code_valid:
            raise ValueError("RESTORE REFUSÉ : Code de confirmation incorrect. Saisissez 'RESTAURER' ou le nom de l'école.")

    target_ecole = Ecole.query.get(ecole_id)
    if not target_ecole:
        raise ValueError(f"École cible (ID #{ecole_id}) introuvable.")

    # Sauvegarde automatique de sécurité avant la restauration (type safety_restore, hors rotation automatic)
    try:
        safety_file = create_school_backup(ecole_id, backup_type="safety_restore")
        log_action("SAUVEGARDE_SECURITE", f"Sauvegarde de sécurité créée avant restauration: {os.path.basename(safety_file)}")
    except Exception as e:
        raise ValueError(f"Échec de la sauvegarde de sécurité préalable : {e}. Restauration annulée par sécurité.")

    try:
        # Suppression des données existantes de l'école dans l'ordre inverse des FK
        db.session.execute(professeur_classes.delete().where(professeur_classes.c.ecole_id == ecole_id))

        user_ids_subq = db.session.query(Utilisateur.id).filter_by(ecole_id=ecole_id)
        eleve_ids_subq = db.session.query(Eleve.id).filter_by(ecole_id=ecole_id)
        classe_ids_subq = db.session.query(Classe.id).filter_by(ecole_id=ecole_id)

        SyncOperationLog.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        JournalCorrection.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        SupportTicket.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        EcoleGoogleMailConfig.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        Alerte.query.filter((Alerte.eleve_id.in_(eleve_ids_subq)) | (Alerte.utilisateur_id.in_(user_ids_subq))).delete(synchronize_session=False)
        Presence.query.filter(Presence.eleve_id.in_(eleve_ids_subq)).delete(synchronize_session=False)
        ArchiveNote.query.filter(ArchiveNote.eleve_id.in_(eleve_ids_subq)).delete(synchronize_session=False)
        ArchiveAbsence.query.filter(ArchiveAbsence.eleve_id.in_(eleve_ids_subq)).delete(synchronize_session=False)
        HistoriqueImport.query.filter(HistoriqueImport.utilisateur_id.in_(user_ids_subq)).delete(synchronize_session=False)

        Bulletin.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Paiement.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Absence.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Note.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        EmploiTemps.query.filter((EmploiTemps.ecole_id == ecole_id) | (EmploiTemps.classe_id.in_(classe_ids_subq))).delete(synchronize_session=False)
        Cours.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        Inscription.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Eleve.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Classe.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        PeriodeBulletin.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        AnneeNiveauConfig.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        AnneeScolaire.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        EcoleNiveauConfig.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        Professeur.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Utilisateur.query.filter_by(ecole_id=ecole_id).filter(Utilisateur.role != 'super_admin').delete(synchronize_session=False)

        # Réinsertion des données sauvegardées
        model_mapping = [
            (Utilisateur, data.get('utilisateurs', [])),
            (Professeur, data.get('professeurs', [])),
            (EcoleNiveauConfig, data.get('ecole_niveau_configs', [])),
            (AnneeScolaire, data.get('annees_scolaires', [])),
            (AnneeNiveauConfig, data.get('annee_niveau_configs', [])),
            (PeriodeBulletin, data.get('periodes_bulletin', [])),
            (Classe, data.get('classes', [])),
            (Eleve, data.get('eleves', [])),
            (Inscription, data.get('inscriptions', [])),
            (Cours, data.get('cours', [])),
            (EmploiTemps, data.get('emplois_temps', [])),
            (Note, data.get('notes', [])),
            (Absence, data.get('absences', [])),
            (Presence, data.get('presences', [])),
            (Paiement, data.get('paiements', [])),
            (Bulletin, data.get('bulletins', [])),
            (ArchiveNote, data.get('archive_notes', [])),
            (ArchiveAbsence, data.get('archive_absences', [])),
            (Alerte, data.get('alertes', [])),
            (EcoleGoogleMailConfig, data.get('ecole_google_mail_configs', [])),
            (JournalCorrection, data.get('journal_corrections', [])),
            (SyncOperationLog, data.get('sync_operation_logs', [])),
            (SupportTicket, data.get('support_tickets', [])),
            (HistoriqueImport, data.get('historique_imports', [])),
        ]

        for model_cls, rows in model_mapping:
            for r in rows:
                obj = _deserialize_row(model_cls, r)
                db.session.add(obj)

        for pc in data.get('professeur_classes', []):
            date_assign = pc.get('date_assignation')
            if date_assign and isinstance(date_assign, str):
                try:
                    date_assign = datetime.fromisoformat(date_assign)
                except ValueError:
                    date_assign = None
            db.session.execute(professeur_classes.insert().values(
                professeur_id=pc['professeur_id'],
                classe_id=pc['classe_id'],
                date_assignation=date_assign or datetime.utcnow(),
                ecole_id=pc['ecole_id']
            ))

        if data.get('ecole'):
            ec = _deserialize_row(Ecole, data['ecole'])
            existing = Ecole.query.get(ecole_id)
            if existing:
                for col in Ecole.__table__.columns:
                    if col.name != 'id':
                        setattr(existing, col.name, getattr(ec, col.name))

        db.session.commit()
        log_action("RESTAURATION", f"Restauration de l'école '{ecole_nom}' (ID={ecole_id}) réussie depuis {filename}")
        return True
    except Exception as e:
        db.session.rollback()
        log_action("ERREUR_RESTAURATION", f"Échec restauration école {ecole_id} : {e}", level="ERROR")
        raise e

def get_school_backups(ecole_id):
    """Récupère la liste des sauvegardes pour une école spécifique"""
    backups = []

    for file in os.listdir(BACKUP_DIR):
        if file.startswith(f'school_{ecole_id}_') and file.endswith('.json'):
            file_path = os.path.join(BACKUP_DIR, file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f).get('metadata', {})

                ts_raw = metadata.get('created_at') or metadata.get('timestamp')
                try:
                    ts_obj = datetime.fromisoformat(ts_raw) if ts_raw else None
                except ValueError:
                    ts_obj = None

                ts_display = ts_obj.strftime('%d/%m/%Y %H:%M:%S') if ts_obj else "Inconnu"
                b_type = metadata.get('backup_type', 'manual')
                b_label = 'Automatique' if b_type == 'automatic' else ('Sécurité Restore' if b_type == 'safety_restore' else 'Manuel')
                badge_class = 'bg-success' if b_type == 'automatic' else ('bg-warning text-dark' if b_type == 'safety_restore' else 'bg-primary')

                backups.append({
                    'filename': file,
                    'ecole_nom': metadata.get('ecole_nom', 'Inconnu'),
                    'backup_type': b_type,
                    'backup_type_label': b_label,
                    'badge_class': badge_class,
                    'timestamp': ts_display,
                    'timestamp_sort': ts_obj or datetime.min,
                    'size': os.path.getsize(file_path),
                    'size_kb': round(os.path.getsize(file_path) / 1024, 1),
                })
            except (OSError, ValueError) as e:
                current_app.logger.warning(f"Backup ignoré car illisible ({file_path}): {e}")
                continue

    backups.sort(key=lambda x: x['timestamp_sort'], reverse=True)
    return backups


# ====================================================================
# 🛠️ GESTION DU MODE MAINTENANCE, SAUVEGARDE AUTO & CACHE
# ====================================================================

CACHE_DIR = os.path.join(BASE_DIR, "app", "static", "qrcache")

def get_param(cle, default=None):
    try:
        p = ParametreSysteme.query.filter_by(cle=cle).first()
        return p.valeur if p else default
    except Exception:
        return default

def set_param(cle, valeur, description=None):
    try:
        p = ParametreSysteme.query.filter_by(cle=cle).first()
        if p:
            p.valeur = str(valeur)
            if description:
                p.description = description
        else:
            p = ParametreSysteme(cle=cle, valeur=str(valeur), description=description)
            db.session.add(p)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur set_param({cle}): {e}")
        return False

def get_maintenance_status():
    active = get_param('maintenance_mode', 'false') == 'true'
    message = get_param('maintenance_message', 'Mise à jour programmée en cours. Nos services seront rétablis sous peu.')
    updated_at = get_param('maintenance_updated_at', '')
    return {
        'active': active,
        'message': message,
        'updated_at': updated_at
    }

def set_maintenance_status(active: bool, message: str = None):
    set_param('maintenance_mode', 'true' if active else 'false', 'Mode maintenance actif')
    if message:
        set_param('maintenance_message', message, 'Message affiché en mode maintenance')
    set_param('maintenance_updated_at', datetime.now().strftime('%d/%m/%Y à %H:%M'), 'Dernière mise à jour maintenance')
    action = "ACTIVATION" if active else "DÉSACTIVATION"
    log_action(f"MAINTENANCE_{action}", f"Mode maintenance {'activé' if active else 'désactivé'}")

def get_auto_backup_config():
    enabled = get_param('auto_backup_enabled', 'true') == 'true'
    time_val = get_param('auto_backup_time', '02:00')
    last_date = get_param('auto_backup_last_date', 'Aucune')
    return {
        'enabled': enabled,
        'time': time_val,
        'last_date': last_date
    }

def set_auto_backup_config(enabled: bool, time_val: str = "02:00"):
    set_param('auto_backup_enabled', 'true' if enabled else 'false', 'Sauvegarde auto quotidienne')
    if time_val:
        set_param('auto_backup_time', time_val, 'Heure sauvegarde auto')
    log_action("CONFIG_BACKUP", f"Sauvegarde auto {'activée' if enabled else 'désactivée'} à {time_val}")

def check_and_run_daily_backup():
    """Vérifie si une sauvegarde quotidienne automatique doit être exécutée"""
    try:
        enabled = get_param('auto_backup_enabled', 'true') == 'true'
        if not enabled:
            return False

        target_time = get_param('auto_backup_time', '02:00')
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        last_date = get_param('auto_backup_last_date', '')

        if last_date == today_str:
            return False

        parts = target_time.split(':')
        target_h = int(parts[0]) if len(parts) > 0 else 2
        target_m = int(parts[1]) if len(parts) > 1 else 0

        if (now.hour > target_h) or (now.hour == target_h and now.minute >= target_m):
            run_daily_automatic_backups()
            set_param('auto_backup_last_date', today_str, 'Dernière exécution sauvegarde auto')
            return True
    except Exception as e:
        current_app.logger.error(f"Erreur check_and_run_daily_backup: {e}")
    return False

def get_cache_info():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)
        return {'count': 0, 'size_kb': 0, 'size_mb': 0}
    
    files = [f for f in os.listdir(CACHE_DIR) if os.path.isfile(os.path.join(CACHE_DIR, f))]
    total_size = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
    return {
        'count': len(files),
        'size_kb': round(total_size / 1024, 1),
        'size_mb': round(total_size / (1024 * 1024), 2)
    }

def purge_cache():
    if not os.path.exists(CACHE_DIR):
        return {'deleted': 0, 'freed_kb': 0, 'freed_mb': 0}
    
    count = 0
    freed = 0
    for f in os.listdir(CACHE_DIR):
        p = os.path.join(CACHE_DIR, f)
        if os.path.isfile(p):
            try:
                freed += os.path.getsize(p)
                os.remove(p)
                count += 1
            except Exception as e:
                current_app.logger.warning(f"Impossible de supprimer {p}: {e}")
                
    log_action("CACHE_PURGE", f"Cache purgé: {count} fichiers supprimés ({round(freed / 1024, 1)} Ko libérés)")
    return {
        'deleted': count,
        'freed_kb': round(freed / 1024, 1),
        'freed_mb': round(freed / (1024 * 1024), 2)
    }

def get_database_health():
    health = {
        'size_mb': 0,
        'integrity': 'Inconnu',
        'table_count': 0,
        'db_version': 'SQLite',
        'stats': {}
    }
    try:
        if os.path.exists(DB_PATH):
            health['size_mb'] = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        row = cur.fetchone()
        health['integrity'] = 'Valide (OK)' if row and row[0] == 'ok' else (row[0] if row else 'Erreur')
        
        cur.execute("SELECT sqlite_version()")
        v = cur.fetchone()
        health['db_version'] = f"SQLite {v[0]}" if v else "SQLite"
        
        cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
        health['table_count'] = cur.fetchone()[0]
        conn.close()
        
        health['stats'] = {
            'ecoles': Ecole.query.count(),
            'utilisateurs': Utilisateur.query.count(),
            'eleves': Eleve.query.count(),
            'professeurs': Professeur.query.count(),
        }
    except Exception as e:
        health['integrity'] = f"Erreur: {e}"
    return health

def get_all_backups_list():
    backups = []
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        return backups
        
    for fname in os.listdir(BACKUP_DIR):
        fpath = os.path.join(BACKUP_DIR, fname)
        if os.path.isfile(fpath) and (fname.endswith('.db') or fname.endswith('.json')):
            stat = os.stat(fpath)
            dt = datetime.fromtimestamp(stat.st_mtime)
            is_school = fname.startswith('school_')
            b_type = 'manual'
            ecole_nom = None
            if fname.endswith('.json'):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        meta = json.load(f).get('metadata', {})
                    b_type = meta.get('backup_type', 'manual')
                    ecole_nom = meta.get('ecole_nom')
                except Exception:
                    pass

            b_label = 'Automatique' if b_type == 'automatic' else ('Sécurité Restore' if b_type == 'safety_restore' else 'Manuel')
            type_display = f"École ({b_label})" if is_school else "Complète"
            badge_class = 'bg-success' if b_type == 'automatic' else ('bg-info' if is_school else 'bg-primary')

            backups.append({
                'filename': fname,
                'ecole_nom': ecole_nom,
                'backup_type': b_type,
                'backup_type_label': b_label,
                'size_kb': round(stat.st_size / 1024, 1),
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'date_formatted': dt.strftime('%d/%m/%Y à %H:%M:%S'),
                'mtime': stat.st_mtime,
                'type': type_display,
                'badge_class': badge_class
            })
    backups.sort(key=lambda x: x['mtime'], reverse=True)
    return backups



