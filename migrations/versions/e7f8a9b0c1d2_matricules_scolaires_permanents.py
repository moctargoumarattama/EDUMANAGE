"""Matricules scolaires permanents AA-XXXX, par école et année."""
from alembic import op

revision = 'e7f8a9b0c1d2'
down_revision = '056e1721ee87'
branch_labels = None
depends_on = None


def upgrade():
    from scripts.migrate_matricules_format import migrate_matricules

    migrate_matricules(op.get_bind())


def downgrade():
    # Un downgrade destructeur supprimerait les identifiants administratifs.
    raise RuntimeError('Les matricules permanents doivent être conservés. Restaurer une sauvegarde pour revenir en arrière.')
