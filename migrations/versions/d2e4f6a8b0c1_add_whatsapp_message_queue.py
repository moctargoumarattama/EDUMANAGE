"""add_whatsapp_message_queue

Revision ID: d2e4f6a8b0c1
Revises: c1d2e3f4a6b7
Create Date: 2026-09-26 18:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd2e4f6a8b0c1'
down_revision = 'c1d2e3f4a6b7'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _columns(table_name):
    if not _table_exists(table_name):
        return set()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col['name'] for col in inspector.get_columns(table_name)}


def upgrade():
    ecole_columns = _columns('ecole')
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        if 'whatsapp_enabled' not in ecole_columns:
            batch_op.add_column(sa.Column('whatsapp_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')))
        if 'whatsapp_sender_phone' not in ecole_columns:
            batch_op.add_column(sa.Column('whatsapp_sender_phone', sa.String(length=30), nullable=True))
        if 'whatsapp_provider' not in ecole_columns:
            batch_op.add_column(sa.Column('whatsapp_provider', sa.String(length=50), nullable=True, server_default='manual'))

    if not _table_exists('message_queue'):
        op.create_table(
            'message_queue',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ecole_id', sa.Integer(), nullable=False),
            sa.Column('destinataire', sa.String(length=30), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('type_message', sa.String(length=50), nullable=True),
            sa.Column('statut', sa.String(length=20), nullable=True),
            sa.Column('tentatives', sa.Integer(), nullable=True),
            sa.Column('max_tentatives', sa.Integer(), nullable=True),
            sa.Column('date_creation', sa.DateTime(), nullable=True),
            sa.Column('date_envoi', sa.DateTime(), nullable=True),
            sa.Column('expire_le', sa.DateTime(), nullable=True),
            sa.Column('erreur_details', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['ecole_id'], ['ecole.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('message_queue', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_message_queue_ecole_id'), ['ecole_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_message_queue_statut'), ['statut'], unique=False)
            batch_op.create_index('ix_message_queue_ecole_statut_creation', ['ecole_id', 'statut', 'date_creation'], unique=False)


def downgrade():
    if _table_exists('message_queue'):
        with op.batch_alter_table('message_queue', schema=None) as batch_op:
            try:
                batch_op.drop_index('ix_message_queue_ecole_statut_creation')
            except Exception:
                pass
            try:
                batch_op.drop_index(batch_op.f('ix_message_queue_statut'))
            except Exception:
                pass
            try:
                batch_op.drop_index(batch_op.f('ix_message_queue_ecole_id'))
            except Exception:
                pass
        op.drop_table('message_queue')

    ecole_columns = _columns('ecole')
    with op.batch_alter_table('ecole', schema=None) as batch_op:
        if 'whatsapp_provider' in ecole_columns:
            batch_op.drop_column('whatsapp_provider')
        if 'whatsapp_sender_phone' in ecole_columns:
            batch_op.drop_column('whatsapp_sender_phone')
        if 'whatsapp_enabled' in ecole_columns:
            batch_op.drop_column('whatsapp_enabled')
