"""add_payment_receipt_verification_token

Revision ID: b4c5d6e7f8a9
Revises: 6a7a4a24b4a7
Create Date: 2026-09-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import secrets


revision = 'b4c5d6e7f8a9'
down_revision = '6a7a4a24b4a7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('paiement', schema=None) as batch_op:
        batch_op.add_column(sa.Column('verification_token', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_paiement_verification_token', ['verification_token'], unique=True)

    bind = op.get_bind()
    paiement = sa.table(
        'paiement',
        sa.column('id', sa.Integer),
        sa.column('verification_token', sa.String(length=64)),
    )
    rows = bind.execute(sa.select(paiement.c.id).where(paiement.c.verification_token.is_(None))).fetchall()
    used = set()
    for row in rows:
        token = secrets.token_urlsafe(32)
        while token in used:
            token = secrets.token_urlsafe(32)
        used.add(token)
        bind.execute(
            paiement.update()
            .where(paiement.c.id == row.id)
            .values(verification_token=token)
        )


def downgrade():
    with op.batch_alter_table('paiement', schema=None) as batch_op:
        batch_op.drop_index('ix_paiement_verification_token')
        batch_op.drop_column('verification_token')
