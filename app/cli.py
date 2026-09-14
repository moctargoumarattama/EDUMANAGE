import click
from flask.cli import with_appcontext


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

