from flask import send_file, current_app, g
from app.utils import get_ecole_filter_query
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import unicodedata
from contextlib import closing
from datetime import datetime, timedelta, date, time
from pathlib import Path
from time import monotonic
from app import db
from app.models import Log, ParametreSysteme
import json
from app.models import Note, Absence, Ecole, Classe, Eleve, Professeur, Utilisateur, AnneeScolaire
from sqlalchemy import text


# --- Configuration ---
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
DB_PATH = os.path.join(BASE_DIR, "instance", "ecole.db")
DEPLOY_SCRIPT = 'scripts/deploy.sh'
os.makedirs('scripts', exist_ok=True)


def slugify_school_name(nom: str) -> str:
    """Transforme le nom d'une école en slug lisible pour les noms de fichier."""
    if not nom:
        return "ecole"
    # Normalisation NFKD puis suppression des accents (caractères non-ASCII)
    slug = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode("ascii")
    # Minuscules, remplacement des non-alphanumériques par des tirets
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    # Tronquer à 35 caractères max (sans couper au milieu d'un tiret)
    slug = slug[:35].rstrip("-")
    return slug or "ecole"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_min_free_mb():
    return int(os.environ.get("BACKUP_MIN_FREE_MB", "512"))


def _global_backup_retention():
    return int(os.environ.get("POSTGRES_BACKUP_RETENTION", os.environ.get("BACKUP_RETENTION_DAYS", "7")))


def _ensure_backup_space():
    usage = shutil.disk_usage(BACKUP_DIR)
    free_mb = usage.free // (1024 * 1024)
    if free_mb < _backup_min_free_mb():
        raise RuntimeError(f"Espace disque insuffisant pour une sauvegarde ({free_mb} Mo libres).")


def _safe_backup_path(filename):
    base = os.path.abspath(BACKUP_DIR)
    path = os.path.abspath(os.path.join(base, filename))
    if not path.startswith(base + os.sep):
        raise ValueError("Nom de sauvegarde invalide.")
    return path


def _cleanup_global_backups(prefixes=("backup_", "klasora-postgresql-"), keep=None, protected_paths=()):
    keep = keep or _global_backup_retention()
    protected = {os.path.realpath(path) for path in protected_paths if path}
    candidates = []
    for name in os.listdir(BACKUP_DIR):
        if not name.startswith(prefixes):
            continue
        if not (name.endswith(".db") or name.endswith(".dump")):
            continue
        path = os.path.join(BACKUP_DIR, name)
        if os.path.isfile(path) and os.path.realpath(path) not in protected:
            candidates.append((os.path.getmtime(path), path))
    candidates.sort(reverse=True)
    deleted = 0
    for _, path in candidates[keep:]:
        try:
            os.remove(path)
            deleted += 1
        except OSError as exc:
            current_app.logger.warning(f"Impossible de supprimer l'ancien backup global {os.path.basename(path)}: {exc}")
    return deleted


class DatabaseBackupBackend:
    def create_global_backup(self):
        raise NotImplementedError

    def restore_global_backup(self, filename):
        raise NotImplementedError

    def health(self):
        raise NotImplementedError


def _sqlite_active_path():
    """Resolve the SQLite file used by Flask, without opening a second database."""
    engine = db.engine
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Cette opération nécessite une base SQLite.")
    filename = engine.url.database
    if not filename or filename == ":memory:" or filename.startswith("file:"):
        raise RuntimeError("La sauvegarde globale nécessite une base SQLite sur disque.")
    return os.path.abspath(filename)


def _open_active_sqlite(timeout=1.0):
    # mode=rw refuses to silently create a database at a stale/configured path.
    uri = Path(_sqlite_active_path()).as_uri() + "?mode=rw"
    return sqlite3.connect(uri, uri=True, timeout=timeout)


def _checkpoint_sqlite(connection):
    # This autonomous connection must never commit the application session.
    try:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.OperationalError as exc:
        if getattr(exc, "sqlite_errorcode", None) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise
        result = (1,)
    if result and result[0]:
        current_app.logger.warning(
            "Checkpoint SQLite occupé ; la sauvegarde utilisera le snapshot SQLite avec son WAL."
        )


def _sqlite_backup_connection(source, destination):
    deadline = monotonic() + 30.0

    def progress(status, remaining, total):
        if monotonic() > deadline:
            raise TimeoutError("Délai dépassé pendant la sauvegarde SQLite.")

    source.backup(destination, pages=256, progress=progress, sleep=0.05)


def _write_sqlite_snapshot(source, filename):
    """Publish a complete snapshot only after every page has been copied."""
    staging = filename + ".tmp"
    try:
        with closing(sqlite3.connect(staging)) as destination:
            _sqlite_backup_connection(source, destination)
        os.replace(staging, filename)
    finally:
        if os.path.exists(staging):
            os.remove(staging)


def _log_global_backup_action(module, action, level="INFO"):
    """Best-effort audit on an independent connection; preserve business work."""
    getattr(current_app.logger, level.lower(), current_app.logger.info)("%s: %s", module, action)
    values = {
        "level": level, "module": module, "action": action,
        "timestamp": datetime.utcnow(), "ip_address": "127.0.0.1",
    }
    try:
        if db.engine.dialect.name == "sqlite":
            with closing(_open_active_sqlite(timeout=0.1)) as connection:
                connection.execute(
                    "INSERT INTO log (level, module, action, timestamp, ip_address) VALUES (?, ?, ?, ?, ?)",
                    (level, module, action, values["timestamp"].isoformat(" "), "127.0.0.1"),
                )
                connection.commit()
        else:
            with db.engine.begin() as connection:
                connection.execute(Log.__table__.insert().values(**values))
    except Exception as exc:
        current_app.logger.warning("Impossible de journaliser la sauvegarde : %s", exc)


class SQLiteBackupBackend(DatabaseBackupBackend):
    name = "sqlite"

    def create_global_backup(self):
        _ensure_backup_space()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
        with closing(_open_active_sqlite()) as source:
            _checkpoint_sqlite(source)
            _write_sqlite_snapshot(source, backup_file)
        checksum = _sha256_file(backup_file)
        _cleanup_global_backups(prefixes=("backup_",), keep=_global_backup_retention())
        _log_global_backup_action("SAUVEGARDE", f"Sauvegarde SQLite creee: {os.path.basename(backup_file)} checksum={checksum}")
        return backup_file

    def restore_global_backup(self, filename):
        backup_file = _safe_backup_path(filename)
        if not os.path.exists(backup_file):
            raise Exception("Fichier de sauvegarde introuvable!")
        if os.path.realpath(backup_file) == os.path.realpath(_sqlite_active_path()):
            raise ValueError("La sauvegarde ne peut pas être la base active.")
        session = db.session()
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("Terminez les modifications en cours avant de restaurer la base.")
        if session.in_transaction() and session.connection().connection.driver_connection.in_transaction:
            raise RuntimeError("Terminez la transaction en cours avant de restaurer la base.")
        _ensure_backup_space()
        current_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        recovery_file = os.path.join(BACKUP_DIR, f"recovery_{current_timestamp}.db")
        uri = Path(backup_file).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as snapshot:
            integrity = snapshot.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("Le fichier de sauvegarde SQLite est invalide.")
            db.session.remove()
            db.engine.dispose()
            try:
                with closing(_open_active_sqlite()) as destination:
                    _checkpoint_sqlite(destination)
                    _write_sqlite_snapshot(destination, recovery_file)
                    # The backup API replaces the database in a write transaction,
                    # including its live WAL, rather than copying behind open handles.
                    _sqlite_backup_connection(snapshot, destination)
                    _checkpoint_sqlite(destination)
            finally:
                db.session.remove()
                db.engine.dispose()
        _log_global_backup_action("RESTAURATION", f"Restauration SQLite depuis: {filename}")
        return True

    def health(self):
        health = {
            "backend": "sqlite",
            "status": "OK",
            "size_mb": round(os.path.getsize(_sqlite_active_path()) / (1024 * 1024), 2),
            "integrity": "Inconnue",
            "db_version": "SQLite",
            "table_count": 0,
        }
        conn = _open_active_sqlite()
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            health["integrity"] = "Valide (OK)" if row and row[0] == "ok" else (row[0] if row else "Erreur")
            cur.execute("SELECT sqlite_version()")
            v = cur.fetchone()
            health["db_version"] = f"SQLite {v[0]}" if v else "SQLite"
            cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
            health["table_count"] = cur.fetchone()[0]
        finally:
            conn.close()
        return health


class PostgreSQLBackupBackend(DatabaseBackupBackend):
    name = "postgresql"

    def __init__(self):
        self.url = db.engine.url

    def _pg_env(self):
        env = os.environ.copy()
        if self.url.password:
            env["PGPASSWORD"] = self.url.password
        return env

    def _connection_args(self):
        args = []
        if self.url.host:
            args += ["--host", self.url.host]
        if self.url.port:
            args += ["--port", str(self.url.port)]
        if self.url.username:
            args += ["--username", self.url.username]
        if self.url.database:
            args += ["--dbname", self.url.database]
        return args

    def create_global_backup(self):
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise RuntimeError("pg_dump introuvable sur le serveur.")
        _ensure_backup_space()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_file = os.path.join(BACKUP_DIR, f"klasora-postgresql-{timestamp}.dump")
        cmd = [pg_dump, "--format=custom", "--no-owner", "--no-privileges", "--file", backup_file] + self._connection_args()
        try:
            result = subprocess.run(cmd, env=self._pg_env(), capture_output=True, text=True, timeout=900, shell=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or "pg_dump a echoue.").strip()[:500])
            if not os.path.exists(backup_file) or os.path.getsize(backup_file) <= 0:
                raise RuntimeError("pg_dump n'a pas cree de fichier valide.")
            checksum = _sha256_file(backup_file)
            _cleanup_global_backups(
                prefixes=("klasora-postgresql-",), keep=_global_backup_retention(),
                protected_paths=(getattr(self, "_restore_source_path", None),),
            )
            _log_global_backup_action("SAUVEGARDE", f"Sauvegarde PostgreSQL creee: {os.path.basename(backup_file)} checksum={checksum}")
            return backup_file
        except Exception:
            if os.path.exists(backup_file):
                try:
                    os.remove(backup_file)
                except OSError:
                    pass
            raise

    def restore_global_backup(self, filename):
        pg_restore = shutil.which("pg_restore")
        if not pg_restore:
            raise RuntimeError("pg_restore introuvable sur le serveur.")
        backup_file = _safe_backup_path(filename)
        if not os.path.exists(backup_file):
            raise Exception("Fichier de sauvegarde introuvable!")
        session = db.session()
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("Terminez les modifications en cours avant de restaurer la base.")
        self._restore_source_path = backup_file
        try:
            safety_file = self.create_global_backup()
        finally:
            self._restore_source_path = None
        cmd = [pg_restore, "--no-owner", "--no-privileges", "--clean", "--if-exists", "--single-transaction", "--exit-on-error"] + self._connection_args() + [backup_file]
        # Release the request's read locks before pg_restore tries to drop tables.
        db.session.remove()
        db.engine.dispose()
        try:
            result = subprocess.run(cmd, env=self._pg_env(), capture_output=True, text=True, timeout=900, shell=False)
        finally:
            db.session.remove()
            db.engine.dispose()
        if result.returncode != 0:
            raise RuntimeError(f"Restauration PostgreSQL echouee. Backup de securite: {os.path.basename(safety_file)}. {(result.stderr or '').strip()[:500]}")
        _log_global_backup_action("RESTAURATION", f"Restauration PostgreSQL depuis: {filename}")
        return True

    def health(self):
        size = db.session.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
        version = db.session.execute(text("SELECT version()")).scalar() or "PostgreSQL"
        table_count = db.session.execute(text("""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """)).scalar() or 0
        return {
            "backend": "postgresql",
            "status": "OK",
            "size_mb": round(size / (1024 * 1024), 2),
            "integrity": "Non applicable (PostgreSQL)",
            "db_version": version.split(" on ")[0],
            "table_count": table_count,
        }


def get_database_backup_backend():
    dialect = db.engine.dialect.name
    if dialect == "sqlite":
        return SQLiteBackupBackend()
    if dialect == "postgresql":
        return PostgreSQLBackupBackend()
    raise RuntimeError(f"Backend de sauvegarde non supporte: {dialect}")

# --- Initialisation des annÃ©es scolaires ---
def init_annees_scolaires():
    """Initialise les annÃ©es scolaires par dÃ©faut"""
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
        log_action("INITIALISATION", "AnnÃ©es scolaires initialisÃ©es")
        return "AnnÃ©es scolaires initialisÃ©es"
    except Exception as e:
        log_action("ERREUR", f"Erreur initialisation annÃ©es: {str(e)}", level="ERROR")
        return f"Erreur: {str(e)}"

# --- CrÃ©ation des tables manquantes ---
def create_missing_tables():
    """CrÃ©e les tables manquantes essentielles"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # CrÃ©er la table annee_scolaire si elle n'existe pas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS annee_scolaire (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom VARCHAR(20) UNIQUE NOT NULL,
                date_debut DATE NOT NULL,
                date_fin DATE NOT NULL,
                statut VARCHAR(20) DEFAULT 'active'
            )
        """)
        
        # CrÃ©er la table session si elle n'existe pas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session (
                id VARCHAR(255) PRIMARY KEY,
                data TEXT,
                expiration DATETIME
            )
        """)
        
        # CrÃ©er la table log si elle n'existe pas
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
        
        # Ajouter la colonne annee_scolaire_id Ã  la table classe si elle n'existe pas
        cursor.execute("PRAGMA table_info(classe)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'annee_scolaire_id' not in columns:
            cursor.execute("ALTER TABLE classe ADD COLUMN annee_scolaire_id INTEGER DEFAULT 1")
        
        conn.commit()
        current_app.logger.info("Tables manquantes crÃ©Ã©es avec succÃ¨s")
        
        # Initialiser les annÃ©es scolaires aprÃ¨s crÃ©ation des tables
        init_annees_scolaires()
        
        return True
        
    except Exception as e:
        current_app.logger.error(f"Erreur crÃ©ation tables: {str(e)}")
        return False
    finally:
        conn.close()

# --- Script de dÃ©ploiement ---
def create_deploy_script():
    script_content = """#!/bin/bash
# Script de dÃ©ploiement pour KLASORA
echo "DÃ©but du dÃ©ploiement Ã  $(date)"

# Mise Ã  jour du code
echo "Mise Ã  jour du code depuis Git..."
git pull origin main

# Installation des dÃ©pendances
echo "Installation des dÃ©pendances..."
pip install -r requirements.txt

# Migration de la base de donnÃ©es
echo "Migration de la base de donnÃ©es..."
flask db upgrade

# RedÃ©marrage du service
echo "RedÃ©marrage du service..."
sudo systemctl restart edumanage

echo "DÃ©ploiement terminÃ© Ã  $(date)"
"""

    with open(DEPLOY_SCRIPT, 'w') as f:
        f.write(script_content)
    
    os.chmod(DEPLOY_SCRIPT, 0o755)
    return DEPLOY_SCRIPT

# --- Sauvegarde ---
def create_backup():
    return get_database_backup_backend().create_global_backup()


# --- Restauration ---
def restore_backup(filename):
    return get_database_backup_backend().restore_global_backup(filename)


# --- Nettoyage (CORRIGÃ‰) ---
def clean_data():
    """Nettoyage des donnÃ©es avec gestion des tables manquantes"""
    log_action("NETTOYAGE", "DÃ©but du nettoyage des donnÃ©es")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Nettoyer les logs de plus d'un an (si la table existe)
        try:
            cursor.execute("DELETE FROM log WHERE timestamp < datetime('now', '-1 year')")
            deleted_logs = cursor.rowcount
        except sqlite3.OperationalError:
            deleted_logs = 0  # Table log n'existe pas encore

        # Nettoyer les donnÃ©es temporaires (si la table session existe)
        try:
            cursor.execute("DELETE FROM session WHERE expiration < datetime('now')")
            deleted_sessions = cursor.rowcount
        except sqlite3.OperationalError:
            deleted_sessions = 0  # Table session n'existe pas

        conn.commit()
        
        result = f"{deleted_logs} logs et {deleted_sessions} sessions nettoyÃ©s"
        log_action("NETTOYAGE", f"Nettoyage terminÃ©: {result}")
        
    except Exception as e:
        result = f"Erreur lors du nettoyage: {str(e)}"
        current_app.logger.error(f"ERREUR NETTOYAGE: {result}")
    finally:
        conn.close()

    return result

# --- Optimisation de la base de donnÃ©es ---
def optimize_database():
    """Optimise la base de donnÃ©es SQLite"""
    if db.engine.dialect.name != "sqlite":
        return True
    try:
        with closing(_open_active_sqlite()) as conn:
            cursor = conn.cursor()
            cursor.execute("VACUUM")
            cursor.execute("PRAGMA optimize")
        
        _log_global_backup_action("OPTIMISATION", "Base de donnÃ©es optimisÃ©e")
        return True
    except Exception as e:
        _log_global_backup_action("ERREUR", f"Erreur optimisation BD: {str(e)}", level="ERROR")
        return False

# --- DÃ©ploiement ---
def deploy_app():
    """ExÃ©cute le script de dÃ©ploiement"""
    try:
        if not os.path.exists(DEPLOY_SCRIPT):
            create_deploy_script()
            
        result = subprocess.run(["bash", DEPLOY_SCRIPT], capture_output=True, text=True)
        
        if result.returncode == 0:
            log_action("DEPLOIEMENT", "DÃ©ploiement rÃ©ussi")
            return result.stdout
        else:
            error_msg = f"Erreur dÃ©ploiement: {result.stderr}"
            log_action("ERREUR", error_msg, level="ERROR")
            return error_msg
            
    except Exception as e:
        error_msg = f"Exception lors du dÃ©ploiement: {str(e)}"
        log_action("ERREUR", error_msg, level="ERROR")
        return error_msg

# --- Journalisation (CORRIGÃ‰) ---
def log_action(module, action, level="INFO", user_id=None, details=None):
    """Journalise une action avec gestion des verrouillages"""
    try:
        # Journaliser d'abord avec le logger systÃ¨me
        if level == "ERROR":
            current_app.logger.error(f"{module}: {action} - {details}")
        elif level == "WARNING":
            current_app.logger.warning(f"{module}: {action} - {details}")
        else:
            current_app.logger.info(f"{module}: {action} - {details}")
        
        # Ensuite, tenter d'Ã©crire dans la table log (si elle existe)
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
            db.session.rollback()
            current_app.logger.warning(f"Impossible d'Ã©crire dans la table log: {db_error}")
            
    except Exception as e:
        import logging
        logging.error(f"Erreur journalisation: {str(e)}")

# --- Statistiques systÃ¨me ---
def get_system_stats():
    stats = {}
    backups = [
        f for f in os.listdir(BACKUP_DIR)
        if f.endswith(('.db', '.dump', '.json'))
    ]
    stats['last_backup'] = max(backups, key=lambda f: os.path.getctime(os.path.join(BACKUP_DIR, f))) if backups else None

    try:
        total, used, free = shutil.disk_usage("/")
        stats['disk_usage'] = round((used / total) * 100, 1)
    except OSError as e:
        current_app.logger.warning(f"Impossible de lire l'utilisation disque: {e}")
        stats['disk_usage'] = "N/A"

    try:
        health = get_database_health()
        backend = health.get('backend') or db.engine.dialect.name
        stats['db_backend'] = 'PostgreSQL' if backend == 'postgresql' else 'SQLite' if backend == 'sqlite' else backend
        stats['db_version'] = health.get('db_version', 'N/A')
        stats['table_count'] = health.get('table_count', 'N/A')
        stats['tables'] = health.get('tables', [])
    except Exception as e:
        current_app.logger.warning(f"Impossible de lire les statistiques base de donnees: {e}")
        stats['db_backend'] = db.engine.dialect.name
        stats['db_version'] = "N/A"
        stats['table_count'] = "N/A"
        stats['tables'] = []

    stats['app_version'] = current_app.config.get('VERSION', 'N/A')
    stats['log_count'] = Log.query.count() if hasattr(Log, 'query') else 0
    stats['log_recent'] = Log.query.filter(Log.timestamp >= datetime.now() - timedelta(days=7)).count() if hasattr(Log, 'query') else 0

    return stats

# --- VÃ©rification d'intÃ©gritÃ© ---
def integrity_check():
    """
    VÃ©rifie l'intÃ©gritÃ© de la base et corrige les colonnes manquantes critiques.
    """
    results = []
    summary = {"total": 0, "success": 0, "errors": 0}

    if db.engine.dialect.name != "sqlite":
        try:
            health = get_database_backup_backend().health()
            results.append(f"Base {health['backend']} accessible : {health['table_count']} tables.")
            summary.update(total=1, success=1)
        except Exception as exc:
            results.append(f"Erreur lors de la vérification : {exc}")
            summary.update(total=1, errors=1)
        return results, summary

    conn = None
    try:
        conn = _open_active_sqlite()
        cursor = conn.cursor()

        # VÃ©rification globale
        cursor.execute("PRAGMA integrity_check")
        row = cursor.fetchone()
        summary["total"] += 1
        if not row:
            results.append("Impossible de vÃ©rifier la base")
            summary["errors"] += 1
        elif row[0] != "ok":
            results.append(f"ProblÃ¨mes dÃ©tectÃ©s: {row[0]} âŒ")
            summary["errors"] += 1
        else:
            results.append("Base de donnÃ©es intÃ¨gre âœ…")
            summary["success"] += 1

        # VÃ©rification colonnes critiques
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
                        results.append(f"Colonne manquante ajoutÃ©e : {table}.{col} âœ…")
                        summary["success"] += 1
                    except Exception as e:
                        results.append(f"Erreur lors de l'ajout de {table}.{col} : {str(e)} âŒ")
                        summary["errors"] += 1
                else:
                    results.append(f"Colonne {table}.{col} OK âœ…")
                    summary["success"] += 1

        conn.close()
        
        _log_global_backup_action("INTEGRITE", f"VÃ©rification d'intÃ©gritÃ©: {summary['success']} succÃ¨s, {summary['errors']} erreurs")
        
    except Exception as e:
        results.append(f"Erreur lors de la vÃ©rification : {str(e)} âŒ")
        summary["errors"] += 1
        _log_global_backup_action("ERREUR", f"Ã‰chec vÃ©rification intÃ©gritÃ©: {str(e)}", level="ERROR")

    finally:
        if conn is not None:
            conn.close()

    return results, summary

# --- Suppression / TÃ©lÃ©chargement sauvegarde ---
def delete_backup_file(filename):
    """Supprime un fichier de sauvegarde"""
    backup_file = _safe_backup_path(filename)
    if os.path.exists(backup_file):
        os.remove(backup_file)
        log_action("SAUVEGARDE", f"Sauvegarde supprimÃ©e: {filename}")
        return True
    else:
        raise Exception("Fichier de sauvegarde introuvable!")

def download_backup_file(filename):
    """TÃ©lÃ©charge un fichier de sauvegarde"""
    backup_file = _safe_backup_path(filename)
    if os.path.exists(backup_file):
        return send_file(backup_file, as_attachment=True, download_name=filename)
    else:
        raise Exception("Fichier de sauvegarde introuvable!")

# --- Sauvegardes par Ã©cole ---
def create_complete_backup():
    """Sauvegarde de toute la base via le backend global de maintenance."""
    return create_backup()


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
    Conserve les `keep` plus rÃ©centes sauvegardes automatiques d'une Ã©cole
    et supprime les plus anciennes sauvegardes automatiques de cette mÃªme Ã©cole.
    Ne touche PAS aux sauvegardes manuelles, safety_restore ou aux autres Ã©coles.
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
                log_action("BACKUP_AUTO_CLEANUP", f"Ancienne sauvegarde automatique supprimÃ©e pour l'Ã©cole ID={ecole_id}: {b['file']}")
            except OSError as e:
                current_app.logger.warning(f"Impossible de supprimer {b['file_path']}: {e}")

    return deleted_count


def run_daily_automatic_backups():
    """
    ExÃ©cute la sauvegarde automatique quotidienne pour toutes les Ã©coles
    (y compris suspendues et en maintenance).
    ExÃ©cution isolÃ©e par Ã©cole : l'Ã©chec d'une Ã©cole ne bloque pas les autres.
    """
    from app.models import Ecole
    ecoles = Ecole.query.all()
    results = {'success': 0, 'failed': 0, 'skipped': 0, 'details': []}

    log_action("BACKUP_AUTO_START", f"DÃ©but de la sauvegarde automatique quotidienne pour {len(ecoles)} Ã©cole(s).")

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
                log_action("BACKUP_AUTO_SKIPPED", f"Sauvegarde automatique dÃ©jÃ  effectuÃ©e aujourd'hui pour l'Ã©cole '{ecole.nom}' (ID={ecole.id})")
                continue

            backup_file = create_school_backup(ecole.id, backup_type="automatic")
            results['success'] += 1
            results['details'].append({'ecole_id': ecole.id, 'ecole_nom': ecole.nom, 'status': 'success', 'file': os.path.basename(backup_file)})
            log_action("BACKUP_AUTO_SUCCESS", f"Sauvegarde automatique rÃ©ussie pour l'Ã©cole '{ecole.nom}' (ID={ecole.id})")
        except Exception as e:
            results['failed'] += 1
            results['details'].append({'ecole_id': ecole.id, 'ecole_nom': ecole.nom, 'status': 'failed', 'error': str(e)})
            log_action("BACKUP_AUTO_FAILED", f"Ã‰chec sauvegarde automatique pour l'Ã©cole '{ecole.nom}' (ID={ecole.id}): {e}", level="ERROR")

    log_action("BACKUP_AUTO_END", f"Sauvegarde automatique terminÃ©e: {results['success']} rÃ©ussie(s), {results['skipped']} ignorÃ©e(s), {results['failed']} Ã©chouÃ©e(s).")
    return results


def _serialiser_certificats_ecole(ecole_id, session=None):
    """Conserve les liens papier par matricule, même si les IDs changent."""
    from app.models import CertificatAdministratif, Eleve

    session = session if session is not None else db.session
    certificats = []
    rows = (
        session.query(CertificatAdministratif, Eleve.matricule)
        .outerjoin(Eleve, db.and_(
            Eleve.id == CertificatAdministratif.eleve_id,
            Eleve.ecole_id == CertificatAdministratif.ecole_id,
        ))
        .filter(CertificatAdministratif.ecole_id == ecole_id)
        .order_by(CertificatAdministratif.id)
        .all()
    )
    for certificat, matricule in rows:
        row = _serialize_instance(certificat)
        row['matricule_eleve'] = matricule
        certificats.append(row)
    return certificats


def create_school_backup(ecole_id, backup_type="manual"):
    """Sauvegarde complÃ¨te et sÃ©curisÃ©e des donnÃ©es d'une Ã©cole spÃ©cifique"""
    from app.models import (
        Ecole, Utilisateur, Professeur, AnneeScolaire, AnneeNiveauConfig,
        EcoleNiveauConfig, Classe, Eleve, Inscription, Cours, Note, Absence,
        Paiement, Bulletin, EmploiTemps, PeriodeBulletin, Presence, Alerte,
        ArchiveNote, ArchiveAbsence, EcoleGoogleMailConfig, JournalCorrection,
        SyncOperationLog, SupportTicket, HistoriqueImport, MatriculeSequence,
        PointagePersonnel, FichePaiePersonnel, gestion_ecole, professeur_classes
    )

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Si sauvegarde automatique, vÃ©rifier l'idempotence quotidienne pour cette Ã©cole
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
                        _log_global_backup_action("BACKUP_AUTO_SKIP", f"Sauvegarde automatique dÃ©jÃ  existante aujourd'hui pour l'Ã©cole ID={ecole_id}")
                        return fpath
                except Exception:
                    continue

    from app.admin.backup_database import lecture_snapshot

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    with lecture_snapshot() as snapshot:
        ecole = snapshot.get(Ecole, ecole_id)
        if not ecole:
            raise ValueError("École non trouvée")

        user_ids_subq = snapshot.query(Utilisateur.id).filter_by(ecole_id=ecole_id)
        eleve_ids_subq = snapshot.query(Eleve.id).filter_by(ecole_id=ecole_id)
        classe_ids_subq = snapshot.query(Classe.id).filter_by(ecole_id=ecole_id)
        prof_classes_raw = snapshot.execute(
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
            'ecole_niveau_configs': [_serialize_instance(c) for c in snapshot.query(EcoleNiveauConfig).filter_by(ecole_id=ecole_id).all()],
            'utilisateurs': [_serialize_instance(u) for u in snapshot.query(Utilisateur).filter_by(ecole_id=ecole_id).filter(Utilisateur.role != 'super_admin').all()],
            'gestion_ecole': [dict(row) for row in snapshot.execute(gestion_ecole.select().where(gestion_ecole.c.ecole_id == ecole_id)).mappings()],
            'professeurs': [_serialize_instance(p) for p in snapshot.query(Professeur).filter_by(ecole_id=ecole_id).all()],
            'pointages_personnel': [_serialize_instance(p) for p in snapshot.query(PointagePersonnel).filter_by(ecole_id=ecole_id).all()],
            'fiches_paie_personnel': [_serialize_instance(p) for p in snapshot.query(FichePaiePersonnel).filter_by(ecole_id=ecole_id).all()],
            'annees_scolaires': [_serialize_instance(a) for a in snapshot.query(AnneeScolaire).filter_by(ecole_id=ecole_id).all()],
            'annee_niveau_configs': [_serialize_instance(c) for c in snapshot.query(AnneeNiveauConfig).filter_by(ecole_id=ecole_id).all()],
            'periodes_bulletin': [_serialize_instance(pb) for pb in snapshot.query(PeriodeBulletin).filter_by(ecole_id=ecole_id).all()],
            'classes': [_serialize_instance(c) for c in snapshot.query(Classe).filter_by(ecole_id=ecole_id).all()],
            'eleves': [_serialize_instance(e) for e in snapshot.query(Eleve).filter_by(ecole_id=ecole_id).all()],
            'certificats_administratifs': _serialiser_certificats_ecole(ecole_id, snapshot),
            'matricule_sequences': [_serialize_instance(s) for s in snapshot.query(MatriculeSequence).filter_by(ecole_id=ecole_id).all()],
            'inscriptions': [_serialize_instance(i) for i in snapshot.query(Inscription).filter_by(ecole_id=ecole_id).all()],
            'cours': [_serialize_instance(c) for c in snapshot.query(Cours).filter_by(ecole_id=ecole_id).all()],
            'emplois_temps': [_serialize_instance(et) for et in snapshot.query(EmploiTemps).filter((EmploiTemps.ecole_id == ecole_id) | (EmploiTemps.classe_id.in_(classe_ids_subq))).all()],
            'professeur_classes': prof_classes_data,
            'notes': [_serialize_instance(n) for n in snapshot.query(Note).filter_by(ecole_id=ecole_id).all()],
            'absences': [_serialize_instance(a) for a in snapshot.query(Absence).filter_by(ecole_id=ecole_id).all()],
            'presences': [_serialize_instance(p) for p in snapshot.query(Presence).join(Eleve).filter(Eleve.ecole_id == ecole_id).all()],
            'paiements': [_serialize_instance(p) for p in snapshot.query(Paiement).filter_by(ecole_id=ecole_id).all()],
            'bulletins': [_serialize_instance(b) for b in snapshot.query(Bulletin).filter_by(ecole_id=ecole_id).all()],
            'archive_notes': [_serialize_instance(an) for an in snapshot.query(ArchiveNote).filter(ArchiveNote.eleve_id.in_(eleve_ids_subq)).all()],
            'archive_absences': [_serialize_instance(aa) for aa in snapshot.query(ArchiveAbsence).filter(ArchiveAbsence.eleve_id.in_(eleve_ids_subq)).all()],
            'alertes': [_serialize_instance(a) for a in snapshot.query(Alerte).filter((Alerte.eleve_id.in_(eleve_ids_subq)) | (Alerte.utilisateur_id.in_(user_ids_subq))).all()],
            'ecole_google_mail_configs': [_serialize_instance(g) for g in snapshot.query(EcoleGoogleMailConfig).filter_by(ecole_id=ecole_id).all()],
            'journal_corrections': [_serialize_instance(j) for j in snapshot.query(JournalCorrection).filter_by(ecole_id=ecole_id).all()],
            'sync_operation_logs': [_serialize_instance(s) for s in snapshot.query(SyncOperationLog).filter_by(ecole_id=ecole_id).all()],
            'support_tickets': [_serialize_instance(st) for st in snapshot.query(SupportTicket).filter_by(ecole_id=ecole_id).all()],
            'historique_imports': [_serialize_instance(hi) for hi in snapshot.query(HistoriqueImport).filter(HistoriqueImport.utilisateur_id.in_(user_ids_subq)).all()],
        }

    counts = {k: len(v) if isinstance(v, list) else (1 if v else 0) for k, v in data.items()}
    checksum = _compute_backup_checksum(data)

    metadata = {
        'type': 'school',
        'backup_type': backup_type,
        'version': '2.1',
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

    school_slug = slugify_school_name(ecole.nom)
    backup_file = os.path.join(BACKUP_DIR, f"school_{ecole.id}_{school_slug}_{timestamp}_{backup_type}.json")
    staging = backup_file + '.tmp'
    try:
        with open(staging, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        os.replace(staging, backup_file)
    finally:
        if os.path.exists(staging):
            os.remove(staging)

    _log_global_backup_action("SAUVEGARDE", f"Sauvegarde ({backup_type}) Ã©cole '{ecole.nom}' (ID={ecole.id}) crÃ©Ã©e avec succÃ¨s: {os.path.basename(backup_file)}")

    if backup_type == "automatic":
        cleanup_old_automatic_backups(ecole_id, keep=3)

    return backup_file


def inspect_school_backup(filename):
    """VÃ©rifie la validitÃ©, l'intÃ©gritÃ© et lit les mÃ©tadonnÃ©es d'une sauvegarde d'Ã©cole."""
    backup_file = _safe_backup_path(filename)
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
        raise ValueError("Type de sauvegarde invalide (sauvegarde d'Ã©cole requise)")

    expected_checksum = metadata.get('checksum')
    if expected_checksum:
        actual_checksum = _compute_backup_checksum(data)
        if expected_checksum != actual_checksum:
            raise ValueError("RESTORE REFUSÃ‰ : Fichier de sauvegarde corrompu ou altÃ©rÃ© (checksum invalide).")

    return metadata, data


def restore_school_backup(filename, target_ecole_id=None, confirmation_code=None):
    """
    Restaure une Ã©cole depuis sa sauvegarde avec contrÃ´le d'intÃ©gritÃ©,
    sauvegarde automatique de sÃ©curitÃ© prÃ©alable et confirmation forte.
    """
    from app.models import (
        Ecole, Utilisateur, Professeur, AnneeScolaire, AnneeNiveauConfig,
        EcoleNiveauConfig, Classe, Eleve, Inscription, Cours, Note, Absence,
        Paiement, Bulletin, EmploiTemps, PeriodeBulletin, Presence, Alerte,
        ArchiveNote, ArchiveAbsence, EcoleGoogleMailConfig, JournalCorrection,
        SyncOperationLog, SupportTicket, HistoriqueImport, MatriculeSequence,
        CertificatAdministratif, PointagePersonnel, FichePaiePersonnel, gestion_ecole, professeur_classes
    )
    from app.services.matricule_service import matricule_conforme, verrouiller_ecole
    session = db.session()
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Terminez les modifications en cours avant de restaurer l'établissement.")
    if db.engine.dialect.name == 'sqlite' and session.in_transaction():
        if session.connection().connection.driver_connection.in_transaction:
            raise RuntimeError("Terminez la transaction en cours avant de restaurer l'établissement.")
    metadata, data = inspect_school_backup(filename)
    ecole_id = metadata.get('ecole_id')
    ecole_nom = metadata.get('ecole_nom', '')

    if target_ecole_id and ecole_id != target_ecole_id:
        raise ValueError(f"RESTORE REFUSÃ‰ : La sauvegarde appartient Ã  l'Ã©cole ID #{ecole_id} ('{ecole_nom}'), pas Ã  l'Ã©cole ID #{target_ecole_id}.")

    if confirmation_code is not None:
        code_valid = (
            str(confirmation_code).strip().upper() == "RESTAURER" or
            str(confirmation_code).strip().lower() == ecole_nom.lower()
        )
        if not code_valid:
            raise ValueError("RESTORE REFUSÃ‰ : Code de confirmation incorrect. Saisissez 'RESTAURER' ou le nom de l'Ã©cole.")

    target_ecole = db.session.get(Ecole, ecole_id)
    if not target_ecole:
        raise ValueError(f"Ã‰cole cible (ID #{ecole_id}) introuvable.")

    # Sauvegarde automatique de sÃ©curitÃ© avant la restauration (type safety_restore, hors rotation automatic)
    try:
        safety_file = create_school_backup(ecole_id, backup_type="safety_restore")
        _log_global_backup_action("SAUVEGARDE_SECURITE", f"Sauvegarde de sÃ©curitÃ© crÃ©Ã©e avant restauration: {os.path.basename(safety_file)}")
    except Exception as e:
        raise ValueError(f"Ã‰chec de la sauvegarde de sÃ©curitÃ© prÃ©alable : {e}. Restauration annulÃ©e par sÃ©curitÃ©.")

    try:
        verrouiller_ecole(ecole_id)
        matricules_actuels = dict(db.session.query(Eleve.id, Eleve.matricule).filter_by(ecole_id=ecole_id).all())
        eleves_restaures = []
        for row in data.get('eleves', []):
            copie = dict(row)
            courant = matricules_actuels.get(copie.get('id'))
            sauvegarde = copie.get('matricule')
            if matricule_conforme(courant):
                if sauvegarde and sauvegarde != courant:
                    raise ValueError(
                        f"RESTORE REFUSÉ : le matricule {courant} de l'élève ID #{copie.get('id')} "
                        "est permanent et diffère de la sauvegarde."
                    )
                copie['matricule'] = courant
            eleves_restaures.append(copie)

        # Les anciennes sauvegardes sans certificats conservent les documents
        # actuels. Une liste vide explicite représente, elle, un snapshot vide.
        certificats_restaures = data.get('certificats_administratifs')
        if certificats_restaures is None:
            certificats_restaures = _serialiser_certificats_ecole(ecole_id)
        if not isinstance(certificats_restaures, list):
            raise ValueError("RESTORE REFUSÉ : liste de certificats invalide.")
        references, codes = set(), set()
        for row in certificats_restaures:
            if not isinstance(row, dict) or row.get('ecole_id', ecole_id) != ecole_id:
                raise ValueError("RESTORE REFUSÉ : certificat d'un autre établissement ou invalide.")
            reference, code = row.get('reference'), row.get('code_verification')
            if not reference or not code or reference in references or code in codes:
                raise ValueError("RESTORE REFUSÉ : référence ou code de certificat absent ou dupliqué.")
            references.add(reference)
            codes.add(code)
        if references:
            collision = CertificatAdministratif.query.filter(
                CertificatAdministratif.ecole_id != ecole_id,
                db.or_(
                    CertificatAdministratif.reference.in_(references),
                    CertificatAdministratif.code_verification.in_(codes),
                ),
            ).first()
            if collision:
                raise ValueError("RESTORE REFUSÉ : référence ou code de certificat déjà utilisé par un autre établissement.")

        # Ces dépendances seraient supprimées par CASCADE sur PostgreSQL.
        # Les anciennes sauvegardes les préservent au lieu de les perdre.
        pointages = data.get('pointages_personnel')
        if pointages is None:
            pointages = [_serialize_instance(p) for p in PointagePersonnel.query.filter_by(ecole_id=ecole_id).all()]
        fiches_paie = data.get('fiches_paie_personnel')
        if fiches_paie is None:
            fiches_paie = [_serialize_instance(p) for p in FichePaiePersonnel.query.filter_by(ecole_id=ecole_id).all()]

        # Une restauration ne doit jamais réutiliser un numéro déjà réservé.
        for row in data.get('matricule_sequences', []):
            prefixe = row.get('prefixe')
            numero = int(row.get('dernier_numero', 0))
            if row.get('ecole_id') != ecole_id or not re.fullmatch(r'[0-9]{2}', prefixe or '') or not 0 <= numero <= 9999:
                raise ValueError("RESTORE REFUSÉ : compteur de matricules invalide.")
            sequence = db.session.get(MatriculeSequence, (ecole_id, prefixe))
            if sequence:
                sequence.dernier_numero = max(sequence.dernier_numero, numero)
            else:
                db.session.add(MatriculeSequence(
                    ecole_id=ecole_id, prefixe=prefixe, dernier_numero=numero,
                ))
        db.session.flush()

        # Suppression des donnÃ©es existantes de l'Ã©cole dans l'ordre inverse des FK
        db.session.execute(professeur_classes.delete().where(professeur_classes.c.ecole_id == ecole_id))

        user_ids_subq = db.session.query(Utilisateur.id).filter_by(ecole_id=ecole_id)
        eleve_ids_subq = db.session.query(Eleve.id).filter_by(ecole_id=ecole_id)
        classe_ids_subq = db.session.query(Classe.id).filter_by(ecole_id=ecole_id)

        affectations_actuelles = list(db.session.execute(gestion_ecole.select().where(db.or_(
            gestion_ecole.c.ecole_id == ecole_id,
            gestion_ecole.c.utilisateur_id.in_(user_ids_subq),
        ))).mappings())
        affectations = data.get('gestion_ecole')
        if affectations is None:
            affectations = [dict(row) for row in affectations_actuelles if row['ecole_id'] == ecole_id]
        if not isinstance(affectations, list) or any(row.get('ecole_id') != ecole_id for row in affectations):
            raise ValueError("RESTORE REFUSÉ : affectations de gestion invalides.")
        affectations = affectations + [dict(row) for row in affectations_actuelles if row['ecole_id'] != ecole_id]
        db.session.execute(gestion_ecole.delete().where(db.or_(
            gestion_ecole.c.ecole_id == ecole_id,
            gestion_ecole.c.utilisateur_id.in_(user_ids_subq),
        )))

        log_users = db.session.query(Log.id, Log.utilisateur_id).filter(Log.utilisateur_id.in_(user_ids_subq)).all()
        Log.query.filter(Log.utilisateur_id.in_(user_ids_subq)).update(
            {Log.utilisateur_id: None}, synchronize_session=False,
        )
        PointagePersonnel.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        FichePaiePersonnel.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        SyncOperationLog.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        JournalCorrection.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        SupportTicket.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        EcoleGoogleMailConfig.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        Alerte.query.filter((Alerte.eleve_id.in_(eleve_ids_subq)) | (Alerte.utilisateur_id.in_(user_ids_subq))).delete(synchronize_session=False)
        Presence.query.filter(Presence.eleve_id.in_(eleve_ids_subq)).delete(synchronize_session=False)
        ArchiveNote.query.filter(ArchiveNote.eleve_id.in_(eleve_ids_subq)).delete(synchronize_session=False)
        ArchiveAbsence.query.filter(ArchiveAbsence.eleve_id.in_(eleve_ids_subq)).delete(synchronize_session=False)
        HistoriqueImport.query.filter(HistoriqueImport.utilisateur_id.in_(user_ids_subq)).delete(synchronize_session=False)

        CertificatAdministratif.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Bulletin.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Paiement.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Absence.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Note.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        EmploiTemps.query.filter((EmploiTemps.ecole_id == ecole_id) | (EmploiTemps.classe_id.in_(classe_ids_subq))).delete(synchronize_session=False)
        Inscription.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Cours.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Eleve.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Classe.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        PeriodeBulletin.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        AnneeNiveauConfig.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        AnneeScolaire.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        EcoleNiveauConfig.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)

        Professeur.query.filter_by(ecole_id=ecole_id).delete(synchronize_session=False)
        Utilisateur.query.filter_by(ecole_id=ecole_id).filter(Utilisateur.role != 'super_admin').delete(synchronize_session=False)

        # RÃ©insertion des donnÃ©es sauvegardÃ©es
        model_mapping = [
            (Utilisateur, data.get('utilisateurs', [])),
            (Professeur, data.get('professeurs', [])),
            (EcoleNiveauConfig, data.get('ecole_niveau_configs', [])),
            (AnneeScolaire, data.get('annees_scolaires', [])),
            (AnneeNiveauConfig, data.get('annee_niveau_configs', [])),
            (PeriodeBulletin, data.get('periodes_bulletin', [])),
            (Classe, data.get('classes', [])),
            (Eleve, eleves_restaures),
            (Cours, data.get('cours', [])),
            (Inscription, data.get('inscriptions', [])),
            (PointagePersonnel, pointages),
            (FichePaiePersonnel, fiches_paie),
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

        db.session.flush()
        user_ids_restaures = {row[0] for row in db.session.query(Utilisateur.id).filter_by(ecole_id=ecole_id).all()}
        for log_id, user_id in log_users:
            if user_id in user_ids_restaures:
                db.session.execute(Log.__table__.update().where(Log.id == log_id).values(utilisateur_id=user_id))
        utilisateurs_existants = {row[0] for row in db.session.query(Utilisateur.id).all()}
        affectations_uniques = set()
        for row in affectations:
            cle = (row.get('utilisateur_id'), row.get('ecole_id'))
            if cle[0] not in utilisateurs_existants or cle in affectations_uniques:
                continue
            db.session.execute(gestion_ecole.insert().values(utilisateur_id=cle[0], ecole_id=cle[1]))
            affectations_uniques.add(cle)
        eleve_map = dict(db.session.query(Eleve.matricule, Eleve.id).filter_by(ecole_id=ecole_id).all())
        anciens_matricules = {row.get('id'): row.get('matricule') for row in eleves_restaures}
        for row in certificats_restaures:
            # Un ancien eleve_id ne remplace jamais un matricule explicite.
            matricule = row.get('matricule_eleve')
            if 'matricule_eleve' not in row:
                matricule = anciens_matricules.get(row.get('eleve_id'))
            eleve_id = eleve_map.get(matricule)
            if not eleve_id:
                current_app.logger.warning(
                    "Certificat %s ignoré pendant la restauration de l'école %s : élève introuvable (matricule %s).",
                    row.get('reference'), ecole_id, matricule,
                )
                continue
            copie = dict(row)
            copie.pop('id', None)
            copie.update(ecole_id=ecole_id, eleve_id=eleve_id)
            db.session.add(_deserialize_row(CertificatAdministratif, copie))

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
            existing = db.session.get(Ecole, ecole_id)
            if existing:
                for col in Ecole.__table__.columns:
                    if col.name != 'id':
                        setattr(existing, col.name, getattr(ec, col.name))

        from app.admin.backup_database import aligner_sequences_postgresql

        db.session.flush()
        aligner_sequences_postgresql(
            db.session.connection(), [model for model, _ in model_mapping] + [CertificatAdministratif],
        )
        db.session.commit()
        _log_global_backup_action("RESTAURATION", f"Restauration de l'Ã©cole '{ecole_nom}' (ID={ecole_id}) rÃ©ussie depuis {filename}")
        return True
    except Exception as e:
        db.session.rollback()
        _log_global_backup_action("ERREUR_RESTAURATION", f"Ã‰chec restauration Ã©cole {ecole_id} : {e}", level="ERROR")
        raise e

def get_school_backups(ecole_id):
    """RÃ©cupÃ¨re la liste des sauvegardes pour une Ã©cole spÃ©cifique"""
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
                b_label = 'Automatique' if b_type == 'automatic' else ('Sécurité' if b_type == 'safety_restore' else 'Manuel')
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
                current_app.logger.warning(f"Backup ignorÃ© car illisible ({file_path}): {e}")
                continue

    backups.sort(key=lambda x: x['timestamp_sort'], reverse=True)
    return backups


# ====================================================================
# GESTION DU MODE MAINTENANCE, SAUVEGARDE AUTO & CACHE
# ====================================================================

CACHE_DIR = os.path.join(BASE_DIR, "app", "static", "qrcache")

def get_param(cle, default=None):
    try:
        try:
            if hasattr(g, '_system_params') and cle in g._system_params:
                return g._system_params[cle]
        except RuntimeError:
            pass

        p = ParametreSysteme.query.filter_by(cle=cle).first()
        val = p.valeur if p else default

        try:
            if not hasattr(g, '_system_params'):
                g._system_params = {}
            g._system_params[cle] = val
        except RuntimeError:
            pass

        return val
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

        try:
            if hasattr(g, '_system_params'):
                g._system_params[cle] = str(valeur)
            if hasattr(g, '_maintenance_status') and cle.startswith('maintenance_'):
                del g._maintenance_status
        except RuntimeError:
            pass

        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Erreur set_param({cle}): {e}")
        return False

def get_maintenance_status():
    try:
        if hasattr(g, '_maintenance_status') and g._maintenance_status is not None:
            return g._maintenance_status
    except RuntimeError:
        pass

    active = get_param('maintenance_mode', 'false') == 'true'
    if not active:
        res = {
            'active': False,
            'message': '',
            'updated_at': ''
        }
    else:
        message = get_param('maintenance_message', 'Mise à jour programmée en cours. Nos services seront rétablis sous peu.')
        updated_at = get_param('maintenance_updated_at', '')
        res = {
            'active': True,
            'message': message,
            'updated_at': updated_at
        }

    try:
        g._maintenance_status = res
    except RuntimeError:
        pass

    return res

def set_maintenance_status(active: bool, message: str = None):
    set_param('maintenance_mode', 'true' if active else 'false', 'Mode maintenance actif')
    if message:
        set_param('maintenance_message', message, 'Message affiché en mode maintenance')
    set_param('maintenance_updated_at', datetime.now().strftime('%d/%m/%Y à %H:%M'), 'Dernière mise à jour maintenance')
    
    try:
        if hasattr(g, '_maintenance_status'):
            del g._maintenance_status
        if hasattr(g, '_system_params'):
            del g._system_params
    except RuntimeError:
        pass
        
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
        try:
            if hasattr(g, '_backup_checked_in_request'):
                return False
            g._backup_checked_in_request = True
        except RuntimeError:
            pass

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
    from app.services.qr_cache import get_qr_cache_stats
    stats = get_qr_cache_stats()
    return {
        'count': stats['count'],
        'size_kb': stats['size_kb'],
        'size_mb': stats['size_mb']
    }

def purge_cache():
    from app.services.qr_cache import cleanup_qr_cache
    res = cleanup_qr_cache(max_age_days=0)
    log_action("CACHE_PURGE", f"Cache purgé: {res['deleted']} fichiers supprimés ({res['freed_kb']} Ko libérés)")
    return {
        'deleted': res['deleted'],
        'freed_kb': res['freed_kb'],
        'freed_mb': res['freed_mb']
    }

def get_database_health():
    try:
        health = get_database_backup_backend().health()
        health['stats'] = {
            'ecoles': Ecole.query.count(),
            'utilisateurs': Utilisateur.query.count(),
            'eleves': Eleve.query.count(),
            'professeurs': Professeur.query.count(),
        }
        return health
    except Exception as e:
        return {
            'backend': db.engine.dialect.name,
            'size_mb': 0,
            'integrity': f"Erreur: {e}",
            'table_count': 0,
            'db_version': 'N/A',
            'stats': {}
        }
def get_all_backups_list():
    backups = []
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        return backups
        
    for fname in os.listdir(BACKUP_DIR):
        fpath = os.path.join(BACKUP_DIR, fname)
        if os.path.isfile(fpath) and (fname.endswith('.db') or fname.endswith('.json') or fname.endswith('.dump')):
            stat = os.stat(fpath)
            dt = datetime.fromtimestamp(stat.st_mtime)
            is_school = fname.startswith('school_')
            is_postgresql = fname.startswith('klasora-postgresql-') and fname.endswith('.dump')
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

            b_label = 'Automatique' if b_type == 'automatic' else ('Sécurité' if b_type == 'safety_restore' else 'Manuel')
            type_display = f"École ({b_label})" if is_school else ("PostgreSQL" if is_postgresql else "Complète")
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

