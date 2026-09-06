"""drop cours enseignant_id

Revision ID: e5a8b92c4f10
Revises: 6a2c8f1d9e34
Create Date: 2026-09-06 22:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5a8b92c4f10'
down_revision = '6a2c8f1d9e34'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cours', schema=None) as batch_op:
        batch_op.drop_column('enseignant_id')


def downgrade():
    with op.batch_alter_table('cours', schema=None) as batch_op:
        batch_op.add_column(sa.Column('enseignant_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_cours_enseignant_id',
            'utilisateur',
            ['enseignant_id'],
            ['id']
        )
