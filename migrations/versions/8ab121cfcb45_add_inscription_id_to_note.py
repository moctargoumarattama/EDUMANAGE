"""add_inscription_id_to_note

Revision ID: 8ab121cfcb45
Revises: 15ae5fdad90d
Create Date: 2026-09-12 19:37:00.000000

Phase 3C : Annualisation du module Notes via Note.inscription_id.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8ab121cfcb45'
down_revision = '15ae5fdad90d'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Nettoyage des anciennes notes de dev orphelines/incompatibles
    conn.execute(sa.text("""
        DELETE FROM note 
        WHERE id NOT IN (
            SELECT n.id FROM note n
            JOIN cours c ON n.cours_id = c.id
            JOIN inscriptions ins ON ins.eleve_id = n.eleve_id 
                                 AND ins.classe_id = c.classe_id 
                                 AND (n.annee_id IS NULL OR ins.annee_scolaire_id = n.annee_id)
        )
    """))

    # 2. Table note : ajout colonne inscription_id avec FK vers inscriptions(id) et index
    with op.batch_alter_table('note', schema=None) as batch_op:
        batch_op.add_column(sa.Column('inscription_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_note_inscription_id',
            'inscriptions',
            ['inscription_id'],
            ['id'],
            ondelete='RESTRICT'
        )
        batch_op.create_index('ix_note_inscription_id', ['inscription_id'], unique=False)

    # 3. Backfill de inscription_id et alignement de annee_id
    conn.execute(sa.text("""
        UPDATE note 
        SET inscription_id = (
            SELECT ins.id 
            FROM cours c 
            JOIN inscriptions ins ON ins.eleve_id = note.eleve_id 
                                 AND ins.classe_id = c.classe_id 
                                 AND (note.annee_id IS NULL OR ins.annee_scolaire_id = note.annee_id)
            WHERE c.id = note.cours_id
        ),
        annee_id = COALESCE(
            note.annee_id,
            (
                SELECT ins.annee_scolaire_id 
                FROM cours c 
                JOIN inscriptions ins ON ins.eleve_id = note.eleve_id 
                                     AND ins.classe_id = c.classe_id 
                WHERE c.id = note.cours_id
            )
        )
        WHERE inscription_id IS NULL
    """))


def downgrade():
    with op.batch_alter_table('note', schema=None) as batch_op:
        batch_op.drop_index('ix_note_inscription_id')
        batch_op.drop_constraint('fk_note_inscription_id', type_='foreignkey')
        batch_op.drop_column('inscription_id')

