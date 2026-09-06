from flask import send_file, current_app
from app.utils import get_ecole_filter_query
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, date
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

def create_school_backup(ecole_id):
    """Sauvegarde des données d'une école spécifique"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ecole = Ecole.query.get(ecole_id)
    
    if not ecole:
        raise Exception("École non trouvée")
    
    backup_file = os.path.join(BACKUP_DIR, f"school_{ecole.id}_{timestamp}.json")
    
    backup_data = {
        'metadata': {
            'type': 'school',
            'ecole_id': ecole.id,
            'ecole_nom': ecole.nom,
            'timestamp': timestamp,
            'version': '1.0'
        },
        'data': {
            'ecole': ecole.to_dict()
        }
    }
    
    try:
        classes = Classe.query.filter_by(ecole_id=ecole_id).all()
        backup_data['data']['classes'] = [classe.to_dict() for classe in classes]
        
        classe_ids = [classe.id for classe in classes]
        eleves = Eleve.query.filter(Eleve.classe_id.in_(classe_ids)).all()
        backup_data['data']['eleves'] = [eleve.to_dict() for eleve in eleves]
        
        eleve_ids = [eleve.id for eleve in eleves]
        notes = Note.query.filter(Note.eleve_id.in_(eleve_ids)).all()
        backup_data['data']['notes'] = [note.to_dict() for note in notes]
        
        absences = Absence.query.filter(Absence.eleve_id.in_(eleve_ids)).all()
        backup_data['data']['absences'] = [absence.to_dict() for absence in absences]
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        log_action("SAUVEGARDE", f"Sauvegarde école {ecole.nom} créée: {backup_file}")
        return backup_file
        
    except Exception as e:
        log_action("ERREUR", f"Erreur sauvegarde école: {str(e)}", level="ERROR")
        raise e

def restore_school_backup(filename):
    """Restauration d'une sauvegarde d'école"""
    backup_file = os.path.join(BACKUP_DIR, filename)
    
    if not os.path.exists(backup_file):
        raise Exception("Fichier de sauvegarde introuvable")
    
    try:
        with open(backup_file, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        if backup_data['metadata']['type'] != 'school':
            raise Exception("Ce n'est pas une sauvegarde d'école")
        
        ecole_data = backup_data['data']['ecole']
        existing_ecole = Ecole.query.get(ecole_data['id'])
        
        if existing_ecole:
            for key, value in ecole_data.items():
                if hasattr(existing_ecole, key) and key != 'id':
                    setattr(existing_ecole, key, value)
        else:
            new_ecole = Ecole(**ecole_data)
            db.session.add(new_ecole)
        
        db.session.commit()
        log_action("RESTAURATION", f"Restauration école depuis: {filename}")
        return f"École {ecole_data['nom']} restaurée avec succès"
        
    except Exception as e:
        db.session.rollback()
        log_action("ERREUR", f"Erreur restauration école: {str(e)}", level="ERROR")
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

                ts_raw = metadata.get('timestamp')
                try:
                    ts_obj = datetime.fromisoformat(ts_raw) if ts_raw else None
                except ValueError:
                    ts_obj = None

                ts_display = ts_obj.strftime('%d/%m/%Y %H:%M:%S') if ts_obj else "Inconnu"

                backups.append({
                    'filename': file,
                    'ecole_nom': metadata.get('ecole_nom', 'Inconnu'),
                    'timestamp': ts_display,
                    'timestamp_sort': ts_obj or datetime.min,
                    'size': os.path.getsize(file_path)
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
            create_backup()
            set_param('auto_backup_last_date', today_str, 'Dernière exécution sauvegarde auto')
            log_action("AUTO_BACKUP", f"Sauvegarde automatique quotidienne exécutée pour le {today_str}")
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
            backups.append({
                'filename': fname,
                'size_kb': round(stat.st_size / 1024, 1),
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'date_formatted': dt.strftime('%d/%m/%Y à %H:%M:%S'),
                'mtime': stat.st_mtime,
                'type': 'École' if is_school else 'Complète',
                'badge_class': 'bg-info' if is_school else 'bg-primary'
            })
    backups.sort(key=lambda x: x['mtime'], reverse=True)
    return backups



