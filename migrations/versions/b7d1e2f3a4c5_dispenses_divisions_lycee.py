"""Séparer les divisions du lycée et tracer les dispenses de matière.

Revision ID: b7d1e2f3a4c5
Revises: e7f8a9b0c1d2
"""
from alembic import op
import sqlalchemy as sa
import re


revision = 'b7d1e2f3a4c5'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('classe', sa.Column('division', sa.String(length=8), nullable=False, server_default=''))
    connexion = op.get_bind()
    anciennes = connexion.execute(sa.text(
        "SELECT c.id, c.section, c.nom FROM classe c "
        "JOIN niveau_scolaire n ON n.id = c.niveau_id "
        "WHERE LOWER(n.cycle) = 'lycee'"
    )).all()
    for classe_id, section, nom in anciennes:
        # A1/A2 sont des séries ; D1/D2 désignent la série D et sa division.
        match = re.fullmatch(r'([CDS])([1-9][0-9]?)', (section or '').strip().upper())
        if not match and (section or '').strip().upper() in {'C', 'D', 'S'}:
            # Certaines anciennes classes portaient déjà « Tle D1 » dans le nom,
            # tandis que la colonne section ne conservait que « D ».
            match = re.search(r'\b([CDS])([1-9][0-9]?)$', (nom or '').strip().upper())
            if match and match.group(1) != section.strip().upper():
                match = None
        if match:
            connexion.execute(sa.text(
                'UPDATE classe SET section = :serie, division = :division WHERE id = :id'
            ), {'serie': match.group(1), 'division': match.group(2), 'id': classe_id})
    op.create_table(
        'dispense_matiere',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ecole_id', sa.Integer(), sa.ForeignKey('ecole.id'), nullable=False),
        sa.Column('annee_id', sa.Integer(), sa.ForeignKey('annee_scolaire.id'), nullable=False),
        sa.Column('inscription_id', sa.Integer(), sa.ForeignKey('inscriptions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('cours_id', sa.Integer(), sa.ForeignKey('cours.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('periode', sa.String(length=50), nullable=False, server_default='*'),
        sa.Column('motif', sa.String(length=50), nullable=False, server_default='medicale'),
        sa.Column('reference_justificatif', sa.String(length=100), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('cree_par_id', sa.Integer(), sa.ForeignKey('utilisateur.id'), nullable=True),
        sa.Column('annulee_par_id', sa.Integer(), sa.ForeignKey('utilisateur.id'), nullable=True),
        sa.Column('date_creation', sa.DateTime(), nullable=False),
        sa.Column('date_annulation', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('inscription_id', 'cours_id', 'periode', name='uq_dispense_inscription_cours_periode'),
    )
    op.create_index('ix_dispense_ecole_annee', 'dispense_matiere', ['ecole_id', 'annee_id'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM dispense_matiere LIMIT 1')).first():
        raise RuntimeError('Downgrade refusé : des dispenses de matière seraient perdues.')
    op.drop_index('ix_dispense_ecole_annee', table_name='dispense_matiere')
    op.drop_table('dispense_matiere')
    op.drop_column('classe', 'division')
