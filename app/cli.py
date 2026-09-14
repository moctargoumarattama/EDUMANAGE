import click
from flask.cli import with_appcontext
from pathlib import Path
import shutil


def _sqlite_path_from_uri(uri):
    if not uri or not uri.startswith("sqlite:///"):
        return None
    return Path(uri.replace("sqlite:///", "", 1))


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
        res = run_daily_automatic_backups()
        click.echo(f"[KLASORA] Sauvegarde terminée : {res['success']} réussie(s), {res['skipped']} déjà faite(s) aujourd'hui, {res['failed']} échouée(s).")
    @app.cli.command("system-health")
    def system_health_command():
        """Affiche un etat runtime leger sans modifier la base."""
        root = Path(app.root_path).parent
        disk = shutil.disk_usage(root)
        sqlite_path = _sqlite_path_from_uri(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        click.echo(f"disk_free_mb={disk.free // (1024 * 1024)}")
        click.echo(f"sqlite_size_mb={_path_size(sqlite_path) // (1024 * 1024) if sqlite_path else 0}")
        click.echo(f"backups_size_mb={_dir_size(root / 'backups') // (1024 * 1024)}")
        click.echo(f"uploads_size_mb={_dir_size(root / 'app' / 'static' / 'ecoles') // (1024 * 1024)}")


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
