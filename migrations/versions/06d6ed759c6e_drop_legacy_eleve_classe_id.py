"""drop_legacy_eleve_classe_id

Revision ID: 06d6ed759c6e
Revises: c78ffb6b7425
Create Date: 2026-09-17 17:15:09.657865

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '06d6ed759c6e'
down_revision = 'c78ffb6b7425'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('eleve', schema=None) as batch_op:
        batch_op.drop_index('ix_eleve_ecole_classe')
        batch_op.drop_column('classe_id')


def downgrade():
    with op.batch_alter_table('eleve', schema=None) as batch_op:
        batch_op.add_column(sa.Column('classe_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_eleve_classe_id', 'classe', ['classe_id'], ['id'], ondelete='RESTRICT')
        batch_op.create_index('ix_eleve_ecole_classe', ['ecole_id', 'classe_id'], unique=False)
