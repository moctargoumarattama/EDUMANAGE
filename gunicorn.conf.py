# gunicorn.conf.py - Configuration de production pour KLASORA (EDUMANAGE)
# ==============================================================================
# OPTIMISÉ POUR SQLITE ET HAUTE PERFORMANCE I/O
# ==============================================================================
import multiprocessing
import os

# Liaison réseau
# Par défaut sur localhost:5005 (proxy Nginx en amont)
bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:5005")
backlog = 2048

# ------------------------------------------------------------------------------
# CONCURRENCE & SÉCURITÉ SQLITE (RÈGLE STRICTE : 1 WORKER)
# ------------------------------------------------------------------------------
# Règle critique pour SQLite :
# - SQLite verrouille l'intégralité du fichier de base de données lors des écritures.
# - Plusieurs workers distincts écrivant simultanément provoquent des erreurs 'database is locked'.
# - En conséquence, TANT QUE SQLITE EST LA BASE ACTIVE, nous conservons strictement 1 worker.
# - Le passage à plusieurs workers sera envisagé lors d'une future migration vers MySQL/PostgreSQL.
workers = int(os.environ.get("GUNICORN_WORKERS", 1))
threads = int(os.environ.get("GUNICORN_THREADS", 1))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "sync")
worker_connections = 1000

# ------------------------------------------------------------------------------
# GESTION DU CYCLE DE VIE DES PROCESSUS
# ------------------------------------------------------------------------------
# Redémarrage périodique des workers pour libérer la mémoire (prévention de fuites)
max_requests = 1000
max_requests_jitter = 50

# Timeouts
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))
graceful_timeout = 30
keepalive = 5

# Fork-safety pour SQLite et SQLAlchemy
# preload_app = False garantit que chaque worker initialise son propre pool SQLite
preload_app = False

# ------------------------------------------------------------------------------
# JOURNALISATION
# ------------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "logs/gunicorn_access.log")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "logs/gunicorn_error.log")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'
capture_output = True

# ------------------------------------------------------------------------------
# NOM DU PROCESSUS
# ------------------------------------------------------------------------------
proc_name = "klasora_gunicorn"
