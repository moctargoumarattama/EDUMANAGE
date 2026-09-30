import click
from flask.cli import with_appcontext
from pathlib import Path
import shutil
from sqlalchemy import text


def _sqlite_path_from_uri(uri):
    if not uri or not uri.startswith("sqlite:///"):
        return None
    return Path(uri.replace("sqlite:///", "", 1))


def _database_dialect(uri):
    return uri.split(":", 1)[0].split("+", 1)[0] if uri else "sqlite"


def _path_size(path):
    if not path or not path.exists() or not path.is_file():
        return 0
    return path.stat().st_size


def _dir_size(path):
    if not path.exists() or not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def register_cli_commands(app):
    """Enregistre les commandes CLI personnalisées de l'application."""

    @app.cli.command("init-system")
    @click.option("--quiet", is_flag=True, help="Mode silencieux")
    def init_system_command(quiet=False):
        """Initialise les données techniques fondamentales (niveaux standards et super administrateur)."""
        _execute_technical_init(quiet=quiet)

    @app.cli.command("init-technical-data")
    @click.option("--quiet", is_flag=True, help="Mode silencieux")
    def init_technical_data_command(quiet=False):
        """Alias pour init-system."""
        _execute_technical_init(quiet=quiet)

    @app.cli.command("backup-schools-daily")
    def backup_schools_daily_command():
        """Exécute la sauvegarde automatique quotidienne pour toutes les écoles (rétention 3 jours)."""
        from app.admin.scripts import run_daily_automatic_backups
        click.echo("[KLASORA] Démarrage de la sauvegarde automatique quotidienne des écoles...")
        res = run_daily_automatic_backups()
        click.echo(f"[KLASORA] Sauvegarde terminée : {res['success']} réussie(s), {res['skipped']} déjà faite(s) aujourd'hui, {res['failed']} échouée(s).")

    @app.cli.command("backup-daily")
    def backup_daily_command():
        """Alias pour backup-schools-daily."""
        from app.admin.scripts import run_daily_automatic_backups
        from app.services.qr_cache import cleanup_qr_cache
        res = run_daily_automatic_backups()
        click.echo(f"[KLASORA] Sauvegarde terminée : {res['success']} réussie(s), {res['skipped']} déjà faite(s) aujourd'hui, {res['failed']} échouée(s).")
        qr_res = cleanup_qr_cache(max_age_days=7)
        if qr_res['deleted'] > 0:
            click.echo(f"[KLASORA] Maintenance QR cache : {qr_res['deleted']} fichier(s) expiré(s) purgé(s) ({qr_res['freed_kb']} Ko libérés).")

    @app.cli.command("cleanup-qrcache")
    @click.option("--max-age-days", default=7, show_default=True, type=int, help="Âge maximal des fichiers en jours avant suppression.")
    @click.option("--all", "purge_all", is_flag=True, help="Purger l'intégralité du cache sans condition d'âge.")
    def cleanup_qrcache_command(max_age_days, purge_all):
        """Purge les fichiers temporaires expirés du cache QR codes (app/static/qrcache)."""
        from app.services.qr_cache import cleanup_qr_cache
        days = 0 if purge_all else max_age_days
        target_label = "TOUT" if purge_all else f"> {days} jour(s)"
        click.echo(f"[KLASORA] Nettoyage du cache QR codes en cours (critère : {target_label})...")
        res = cleanup_qr_cache(max_age_days=days)
        click.echo(
            f"[KLASORA] Nettoyage terminé : {res['deleted']} fichier(s) purgé(s) "
            f"({res['freed_kb']} Ko libérés), {res['remaining']} restant(s), {res['errors']} erreur(s)."
        )

    @app.cli.command("system-health")
    def system_health_command():
        """Affiche un etat runtime leger sans modifier la base."""
        from app.extensions import db

        root = Path(app.root_path).parent
        disk = shutil.disk_usage(root)
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        dialect = _database_dialect(db_uri)
        click.echo(f"disk_free_mb={disk.free // (1024 * 1024)}")
        if dialect == "postgresql":
            size = db.session.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
            click.echo(f"postgres_size_mb={int(size) // (1024 * 1024)}")
        else:
            sqlite_path = _sqlite_path_from_uri(db_uri)
            click.echo(f"sqlite_size_mb={_path_size(sqlite_path) // (1024 * 1024) if sqlite_path else 0}")
        click.echo(f"backups_size_mb={_dir_size(root / 'backups') // (1024 * 1024)}")
        click.echo(f"uploads_size_mb={_dir_size(root / 'app' / 'static' / 'ecoles') // (1024 * 1024)}")

    @app.cli.group("whatsapp")
    def whatsapp_group():
        """Commandes WhatsApp transactionnelles."""

    @whatsapp_group.command("process-queue")
    @click.option("--limit", default=50, show_default=True, type=int, help="Nombre maximum de messages a traiter.")
    @click.option("--ecole-id", default=None, type=int, help="Limiter le depilage a une ecole.")
    def whatsapp_process_queue_command(limit=50, ecole_id=None):
        """Depile la file WhatsApp vers la passerelle locale Baileys."""
        from app.services.whatsapp_queue import envoyer_via_baileys, process_queue

        result = process_queue(envoyer_via_baileys, ecole_id=ecole_id, limit=limit, commit=True)
        click.echo(
            "whatsapp_queue "
            f"processed={len(result['processed'])} "
            f"sent={len(result['sent'])} "
            f"failed={len(result['failed'])} "
            f"pending={len(result['pending'])} "
            f"expired={len(result['expired'])}"
        )


def _execute_technical_init(quiet=False):
    from app.services.niveaux import ensure_standard_niveaux
    from app.init_superadmin import ensure_canonical_superadmin
    from app.models import Ecole, Classe, Eleve, Professeur, NiveauScolaire

    if not quiet:
        click.echo("[KLASORA] Démarrage de l'initialisation technique...")

    # 1. Niveaux scolaires standards
    niveaux = ensure_standard_niveaux(commit=True)
    if not quiet:
        click.echo(f"  [+] Catalogue des niveaux scolaires : {len(niveaux)} niveaux configurés (idempotent)")

    # 2. Super administrateur canonique
    sa = ensure_canonical_superadmin()
    if not quiet:
        if sa:
            click.echo(f"  [+] Super administrateur canonique : {sa.email} (actif, role super_admin)")
        else:
            click.echo("  [!] Avertissement : Impossible d'assurer le super administrateur.")

    # 3. Vérification de pureté : aucune donnée métier ne doit avoir été créée
    nb_ecoles = Ecole.query.count()
    nb_classes = Classe.query.count()
    nb_eleves = Eleve.query.count()
    nb_profs = Professeur.query.count()

    if not quiet:
        if nb_ecoles == 0 and nb_classes == 0 and nb_eleves == 0 and nb_profs == 0:
            click.echo("  [+] Pureté vérifiée : 0 école, 0 classe, 0 élève, 0 professeur créés.")
        else:
            click.echo(f"  [i] Données métier détectées : {nb_ecoles} écoles, {nb_classes} classes, {nb_eleves} élèves, {nb_profs} professeurs.")
        click.echo("[KLASORA] Initialisation technique terminée avec succès.")
