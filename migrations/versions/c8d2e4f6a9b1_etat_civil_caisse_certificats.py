"""Intégrer l'état civil, l'idempotence et les certificats dans Alembic.

Revision ID: c8d2e4f6a9b1
Revises: b7d1e2f3a4c5

Compatible avec les installations ayant déjà exécuté les scripts manuels.
Les ajouts directs conservent notamment les triggers des matricules SQLite.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c8d2e4f6a9b1'
down_revision = 'b7d1e2f3a4c5'
branch_labels = None
depends_on = None


def _colonnes(connexion, table):
    return {colonne['name'] for colonne in sa.inspect(connexion).get_columns(table)}


def _index_partiel(index):
    return any(
        cle.endswith('_where') and valeur is not None
        for cle, valeur in index.get('dialect_options', {}).items()
    )


def _unicite_cle_existante(connexion):
    inspecteur = sa.inspect(connexion)
    return any(
        contrainte.get('column_names') == ['idempotency_key']
        for contrainte in inspecteur.get_unique_constraints('paiement')
    ) or any(
        index.get('unique')
        and index.get('column_names') == ['idempotency_key']
        and not _index_partiel(index)
        for index in inspecteur.get_indexes('paiement')
    )


def _verifier_schema_avant_ajouts(connexion):
    tables = set(sa.inspect(connexion).get_table_names())
    manquantes = {'ecole', 'eleve', 'paiement'} - tables
    if manquantes:
        raise RuntimeError(
            "Migration refusée : tables scolaires manquantes ("
            + ', '.join(sorted(manquantes))
            + "). Appliquez d'abord les migrations précédentes."
        )

    # Avant toute DDL : SQLite ne garantit pas le rollback des ALTER TABLE.
    # Ne jamais choisir ou supprimer arbitrairement un paiement en double.
    if 'idempotency_key' in _colonnes(connexion, 'paiement'):
        doublon = connexion.execute(sa.text(
            'SELECT 1 FROM paiement WHERE idempotency_key IS NOT NULL '
            'GROUP BY idempotency_key HAVING COUNT(*) > 1 LIMIT 1'
        )).first()
        if doublon:
            raise RuntimeError(
                "Migration refusée : des paiements partagent une clé d'opération. "
                "Examinez ces doublons avant de relancer flask db upgrade ; "
                "aucun paiement n'a été supprimé."
            )

    noms_canoniques = {
        'ix_paiement_idempotency_key': ['idempotency_key'],
        'ix_paiement_idempotency': ['ecole_id', 'idempotency_key'],
    }
    for index in sa.inspect(connexion).get_indexes('paiement'):
        colonnes_attendues = noms_canoniques.get(index['name'])
        if colonnes_attendues and index.get('column_names') != colonnes_attendues:
            raise RuntimeError(
                f"Migration refusée : l'index {index['name']} porte sur "
                "d'autres colonnes. Vérifiez son origine avant de poursuivre."
            )


def _ajouter_colonnes_absentes(connexion, table, colonnes):
    existantes = _colonnes(connexion, table)
    for colonne in colonnes:
        if colonne.name not in existantes:
            op.add_column(table, colonne)


def _assurer_index_paiement(connexion, nom, colonnes, unique=False):
    indexes = sa.inspect(connexion).get_indexes('paiement')
    if any(
        index.get('column_names') == colonnes
        and (not unique or index.get('unique'))
        and not _index_partiel(index)
        for index in indexes
    ):
        return
    if any(index['name'] == nom for index in indexes):
        # Ancien script : index ordinaire au lieu de l'unicité attendue,
        # ou index partiel ne couvrant qu'une partie des paiements.
        op.drop_index(nom, table_name='paiement')
    op.create_index(nom, 'paiement', colonnes, unique=unique)


def _colonnes_identite_certificat():
    return [
        sa.Column('nom_eleve', sa.String(100), nullable=True),
        sa.Column('prenom_eleve', sa.String(100), nullable=True),
        sa.Column('matricule_eleve', sa.String(20), nullable=True),
        sa.Column('date_naissance_eleve', sa.Date(), nullable=True),
        sa.Column('lieu_naissance_eleve', sa.String(100), nullable=True),
        sa.Column('nationalite_eleve', sa.String(100), nullable=True),
        sa.Column('genre_eleve', sa.String(10), nullable=True),
        sa.Column('nom_pere_eleve', sa.String(120), nullable=True),
        sa.Column('nom_mere_eleve', sa.String(120), nullable=True),
        sa.Column('numero_acte_eleve', sa.String(100), nullable=True),
    ]


def _creer_table_certificats():
    # Structure figée dans la révision : ne pas importer les modèles courants.
    op.create_table(
        'certificat_administratif',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ecole_id', sa.Integer(), sa.ForeignKey('ecole.id', ondelete='CASCADE'), nullable=False),
        sa.Column('eleve_id', sa.Integer(), sa.ForeignKey('eleve.id', ondelete='CASCADE'), nullable=False),
        sa.Column('type_certificat', sa.String(30), nullable=False),
        sa.Column('reference', sa.String(50), nullable=False),
        sa.Column('annee_scolaire', sa.String(20), nullable=False),
        sa.Column('classe_nom', sa.String(80), nullable=False),
        sa.Column('niveau', sa.String(50), nullable=True),
        *_colonnes_identite_certificat(),
        sa.Column('type_admission', sa.String(30), nullable=True),
        sa.Column('date_depart', sa.Date(), nullable=True),
        sa.Column('etablissement_destination', sa.String(150), nullable=True),
        sa.Column('ville_emission', sa.String(80), nullable=True),
        sa.Column('date_emission', sa.Date(), nullable=True),
        sa.Column('signataire_nom', sa.String(120), nullable=False),
        sa.Column('signataire_titre', sa.String(80), nullable=True),
        sa.Column('code_verification', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    for nom, unique in (
        ('ecole_id', False), ('eleve_id', False),
        ('reference', True), ('code_verification', True),
    ):
        op.create_index(f'ix_certificat_administratif_{nom}', 'certificat_administratif', [nom], unique=unique)


def upgrade():
    connexion = op.get_bind()
    _verifier_schema_avant_ajouts(connexion)
    _ajouter_colonnes_absentes(connexion, 'eleve', [
        # Même défaut lors de l'ajout que le script historique. Si la colonne
        # existe déjà, conserver toutes ses valeurs, y compris NULL et vide.
        sa.Column('nationalite', sa.String(60), nullable=True, server_default='Nigérienne'),
        sa.Column('numero_acte', sa.String(100), nullable=True),
        sa.Column('nom_pere', sa.String(150), nullable=True),
        sa.Column('nom_mere', sa.String(150), nullable=True),
    ])
    _ajouter_colonnes_absentes(connexion, 'paiement', [
        sa.Column('idempotency_key', sa.String(64), nullable=True),
    ])
    if not _unicite_cle_existante(connexion):
        _assurer_index_paiement(connexion, 'ix_paiement_idempotency_key', ['idempotency_key'], unique=True)
    _assurer_index_paiement(connexion, 'ix_paiement_idempotency', ['ecole_id', 'idempotency_key'])

    if sa.inspect(connexion).has_table('certificat_administratif'):
        # Une émission ancienne sans snapshot doit rester identifiable comme
        # telle : ne pas lui attribuer l'identité actuelle de l'élève.
        _ajouter_colonnes_absentes(connexion, 'certificat_administratif', _colonnes_identite_certificat())
    else:
        _creer_table_certificats()


def downgrade():
    raise RuntimeError(
        "Retour arrière refusé : l'état civil, les identités des certificats "
        "et les clés d'opération doivent être conservés. "
        "Pour revenir à une ancienne version, restaurez une sauvegarde compatible."
    )
