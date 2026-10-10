"""Migrations réelles sur SQLite mémoire, sans accès à une base d'école."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.config import TestingConfig
from app.models import CertificatAdministratif


PREVIOUS_REVISION = "b7d1e2f3a4c5"
REVISION = "c8d2e4f6a9b1"
CIVIL_COLUMNS = {
    "nationalite": 60,
    "numero_acte": 100,
    "nom_pere": 150,
    "nom_mere": 150,
}
SNAPSHOT_COLUMNS = {
    "nom_eleve", "prenom_eleve", "matricule_eleve", "date_naissance_eleve",
    "lieu_naissance_eleve", "nationalite_eleve", "genre_eleve",
    "nom_pere_eleve", "nom_mere_eleve", "numero_acte_eleve",
}


class SchemaMigrationTestConfig(TestingConfig):
    SECRET_KEY = "test-etat-civil-caisse-alembic"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


@pytest.fixture
def migration_app():
    app = create_app(SchemaMigrationTestConfig)
    with app.app_context():
        assert str(db.engine.url) == "sqlite:///:memory:"
        assert db.engine.url.database == ":memory:"
        yield app
        db.session.remove()
        db.engine.dispose()


def _cli(app, *arguments):
    return app.test_cli_runner().invoke(args=["db", *arguments])


def _success(result):
    assert result.exit_code == 0, result.output + repr(result.exception)


def _revision():
    with db.engine.connect() as connection:
        return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def _rows(table):
    # Table names here are constants supplied by the tests.
    with db.engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            sa.text(f"SELECT * FROM {table} ORDER BY id")
        ).mappings()]


def _schema_snapshot():
    with db.engine.connect() as connection:
        return connection.execute(sa.text(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )).all()


def _legacy_schema(app, *, certificate=False, civil=False, key=False):
    """Un schéma déjà en service : aucun create_all des modèles actuels."""
    metadata = sa.MetaData()
    # Cette table empêche env.py de traiter la base comme une installation vierge.
    sa.Table("utilisateur", metadata,
             sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("mot_de_passe", sa.String(255)))
    sa.Table("ecole", metadata,
             sa.Column("id", sa.Integer, primary_key=True),
             sa.Column("nom", sa.String(100)))
    eleve_columns = [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ecole_id", sa.Integer, sa.ForeignKey("ecole.id")),
        sa.Column("nom", sa.String(100)),
        sa.Column("prenom", sa.String(100)),
        sa.Column("date_naissance", sa.Date),
        sa.Column("matricule", sa.String(12)),
        sa.Column("code_parent", sa.String(10)),
    ]
    if civil:
        eleve_columns.extend(sa.Column(name, sa.String(length))
                             for name, length in CIVIL_COLUMNS.items())
    sa.Table("eleve", metadata, *eleve_columns)
    paiement_columns = [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ecole_id", sa.Integer, sa.ForeignKey("ecole.id")),
        sa.Column("eleve_id", sa.Integer, sa.ForeignKey("eleve.id")),
        sa.Column("montant", sa.Float),
        sa.Column("statut", sa.String(20)),
        sa.Column("reference", sa.String(100)),
    ]
    if key:
        paiement_columns.append(sa.Column("idempotency_key", sa.String(64)))
    sa.Table("paiement", metadata, *paiement_columns)
    if certificate:
        sa.Table("certificat_administratif", metadata,
                 sa.Column("id", sa.Integer, primary_key=True),
                 sa.Column("ecole_id", sa.Integer, sa.ForeignKey("ecole.id")),
                 sa.Column("eleve_id", sa.Integer, sa.ForeignKey("eleve.id")),
                 sa.Column("type_certificat", sa.String(30)),
                 sa.Column("reference", sa.String(50), unique=True),
                 sa.Column("annee_scolaire", sa.String(20)),
                 sa.Column("classe_nom", sa.String(80)),
                 sa.Column("niveau", sa.String(50)),
                 sa.Column("type_admission", sa.String(30)),
                 sa.Column("date_depart", sa.Date),
                 sa.Column("etablissement_destination", sa.String(150)),
                 sa.Column("ville_emission", sa.String(80)),
                 sa.Column("date_emission", sa.Date),
                 sa.Column("signataire_nom", sa.String(120)),
                 sa.Column("signataire_titre", sa.String(80)),
                 sa.Column("code_verification", sa.String(64), unique=True),
                 sa.Column("created_at", sa.DateTime))
    metadata.create_all(db.engine)
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO utilisateur (id, mot_de_passe) VALUES (1, 'hash-inchange')"
        ))
        connection.execute(sa.text("INSERT INTO ecole (id, nom) VALUES (1, 'École test')"))
        connection.execute(sa.text(
            "INSERT INTO eleve (id, ecole_id, nom, prenom, date_naissance, matricule, code_parent) "
            "VALUES (1, 1, 'Abdou', 'Moussa', '2012-05-10', '25-0001', '58000001')"
        ))
        connection.execute(sa.text(
            "INSERT INTO paiement (id, ecole_id, eleve_id, montant, statut, reference) "
            "VALUES (1, 1, 1, 85000, 'payé', 'RECU-001')"
        ))
        if certificate:
            connection.execute(sa.text(
                "INSERT INTO certificat_administratif "
                "(id, ecole_id, eleve_id, type_certificat, reference, annee_scolaire, "
                "classe_nom, signataire_nom, code_verification) "
                "VALUES (1, 1, 1, 'scolarite', 'CS-2025-0001', '2025-2026', "
                "'6ème A', 'Directeur', 'verification-inchangee')"
            ))
    _success(_cli(app, "stamp", PREVIOUS_REVISION))
    assert _revision() == PREVIOUS_REVISION


def _assert_payment_unique():
    with pytest.raises(IntegrityError):
        with db.engine.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO paiement "
                "(id, ecole_id, eleve_id, montant, statut, idempotency_key) "
                "VALUES (2, 1, 1, 10000, 'annulé', 'operation-a')"
            ))


def test_upgrade_ancien_schema_cree_etat_civil_caisse_et_certificats(migration_app):
    _legacy_schema(migration_app)
    eleve_before = _rows("eleve")
    paiement_before = _rows("paiement")
    users_before = _rows("utilisateur")

    _success(_cli(migration_app, "upgrade"))

    assert _revision() == REVISION
    inspector = sa.inspect(db.engine)
    eleve_columns = {column["name"]: column for column in inspector.get_columns("eleve")}
    for name, length in CIVIL_COLUMNS.items():
        assert eleve_columns[name]["type"].length == length
        assert eleve_columns[name]["nullable"]
    assert _rows("eleve")[0]["nationalite"] == "Nigérienne"
    assert [{name: row[name] for name in eleve_before[0]} for row in _rows("eleve")] == eleve_before
    assert [{name: row[name] for name in paiement_before[0]} for row in _rows("paiement")] == paiement_before
    assert _rows("utilisateur") == users_before

    indexes = inspector.get_indexes("paiement")
    assert any(index["unique"] and index["column_names"] == ["idempotency_key"]
               for index in indexes)
    assert any(index["column_names"] == ["ecole_id", "idempotency_key"]
               for index in indexes)
    cert_columns = {column["name"] for column in inspector.get_columns("certificat_administratif")}
    assert cert_columns == set(CertificatAdministratif.__table__.columns.keys())
    foreign_keys = inspector.get_foreign_keys("certificat_administratif")
    assert {fk["referred_table"] for fk in foreign_keys} == {"ecole", "eleve"}
    assert all(fk.get("options", {}).get("ondelete") == "CASCADE" for fk in foreign_keys)

    with db.engine.begin() as connection:
        connection.execute(sa.text("UPDATE paiement SET idempotency_key = 'operation-a' WHERE id = 1"))
    _assert_payment_unique()


def test_upgrade_certificat_existant_ajoute_snapshots_sans_inventer_identite(migration_app):
    _legacy_schema(migration_app, certificate=True)
    before = _rows("certificat_administratif")

    _success(_cli(migration_app, "upgrade"))

    columns = {column["name"]: column for column in sa.inspect(db.engine).get_columns("certificat_administratif")}
    assert SNAPSHOT_COLUMNS <= set(columns)
    for name in SNAPSHOT_COLUMNS:
        expected = CertificatAdministratif.__table__.columns[name]
        assert columns[name]["nullable"]
        assert isinstance(columns[name]["type"], type(expected.type))
        if isinstance(expected.type, sa.String):
            assert columns[name]["type"].length == expected.type.length
    after = _rows("certificat_administratif")
    assert [{name: row[name] for name in before[0]} for row in after] == before
    assert all(after[0][name] is None for name in SNAPSHOT_COLUMNS)


def test_upgrade_apres_scripts_manuels_preserve_toutes_les_donnees(migration_app):
    from scripts.migrate_caisse_idempotence_et_certificats import migrate as migrate_caisse
    from scripts.migrate_niger_pratique import migrate as migrate_niger

    _legacy_schema(migration_app, certificate=True)
    migrate_niger(migration_app)
    migrate_caisse(migration_app)
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "UPDATE eleve SET nationalite = 'Malienne', numero_acte = '123/2012', "
            "nom_pere = 'Issa', nom_mere = 'Aïcha' WHERE id = 1"
        ))
        connection.execute(sa.text("UPDATE paiement SET idempotency_key = 'operation-a' WHERE id = 1"))
        connection.execute(sa.text(
            "UPDATE certificat_administratif SET nom_eleve = 'Nom scellé', "
            "prenom_eleve = 'Prénom scellé', nationalite_eleve = 'Nigérienne', "
            "matricule_eleve = '25-0001' WHERE id = 1"
        ))
    before = {table: _rows(table) for table in ("eleve", "paiement", "certificat_administratif")}

    _success(_cli(migration_app, "upgrade"))

    assert {table: _rows(table) for table in before} == before
    _assert_payment_unique()


def test_upgrade_conserve_nationalites_nulles_vides_et_renseignees(migration_app):
    _legacy_schema(migration_app, civil=True)
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO eleve (id, ecole_id, nom, nationalite, numero_acte) "
            "VALUES (2, 1, 'Vide', '   ', 'acte-inchange')"
        ))
        connection.execute(sa.text(
            "INSERT INTO eleve (id, ecole_id, nom, nationalite, nom_pere) "
            "VALUES (3, 1, 'Étrangère', 'Béninoise', 'Père inchangé')"
        ))
    before = _rows("eleve")

    _success(_cli(migration_app, "upgrade"))

    assert _rows("eleve") == before


def test_upgrade_repare_index_ordinaire_et_ne_confond_pas_index_partiel(migration_app):
    _legacy_schema(migration_app, key=True)
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE INDEX ix_paiement_idempotency_key ON paiement (idempotency_key)"
        ))
        connection.execute(sa.text(
            "CREATE UNIQUE INDEX uq_paiement_cle_partielle ON paiement (idempotency_key) "
            "WHERE statut = 'payé'"
        ))
        connection.execute(sa.text("UPDATE paiement SET idempotency_key = 'operation-a' WHERE id = 1"))
    before = _rows("paiement")

    _success(_cli(migration_app, "upgrade"))

    assert _rows("paiement") == before
    indexes = sa.inspect(db.engine).get_indexes("paiement")
    assert any(index["unique"] and index["column_names"] == ["idempotency_key"]
               and index.get("dialect_options", {}).get("sqlite_where") is None
               for index in indexes)
    _assert_payment_unique()


def test_doublons_refuses_avant_ddl_sans_changer_version_ni_donnees(migration_app):
    _legacy_schema(migration_app, key=True)
    with db.engine.begin() as connection:
        connection.execute(sa.text("UPDATE paiement SET idempotency_key = 'operation-a' WHERE id = 1"))
        connection.execute(sa.text(
            "INSERT INTO paiement (id, ecole_id, eleve_id, montant, idempotency_key) "
            "VALUES (2, 1, 1, 15000, 'operation-a')"
        ))
    schema_before = _schema_snapshot()
    before = {table: _rows(table) for table in ("utilisateur", "ecole", "eleve", "paiement")}

    result = _cli(migration_app, "upgrade")

    assert result.exit_code != 0
    assert result.exception is not None
    assert _revision() == PREVIOUS_REVISION
    assert _schema_snapshot() == schema_before
    assert {table: _rows(table) for table in before} == before


@pytest.mark.parametrize("key_exists", [True, False])
@pytest.mark.parametrize("index_name", ["ix_paiement_idempotency_key", "ix_paiement_idempotency"])
def test_nom_index_en_collision_refuse_avant_ddl(migration_app, key_exists, index_name):
    _legacy_schema(migration_app, key=key_exists)
    with db.engine.begin() as connection:
        connection.execute(sa.text(f"CREATE INDEX {index_name} ON paiement (reference)"))
    schema_before = _schema_snapshot()
    before = {table: _rows(table) for table in ("eleve", "paiement")}

    result = _cli(migration_app, "upgrade")

    assert result.exit_code != 0
    assert result.exception is not None
    assert _revision() == PREVIOUS_REVISION
    assert _schema_snapshot() == schema_before
    assert {table: _rows(table) for table in before} == before


def test_plusieurs_cles_nulles_restent_autorisees(migration_app):
    _legacy_schema(migration_app)
    _success(_cli(migration_app, "upgrade"))

    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO paiement (id, ecole_id, eleve_id, montant, idempotency_key) "
            "VALUES (2, 1, 1, 10000, NULL), (3, 1, 1, 20000, NULL)"
        ))

    assert len(_rows("paiement")) == 3


def test_deuxieme_upgrade_ne_modifie_plus_schema_ni_donnees(migration_app):
    _legacy_schema(migration_app, certificate=True)
    _success(_cli(migration_app, "upgrade"))
    schema_before = _schema_snapshot()
    before = {table: _rows(table) for table in ("eleve", "paiement", "certificat_administratif")}

    _success(_cli(migration_app, "upgrade"))

    assert _revision() == REVISION
    assert _schema_snapshot() == schema_before
    assert {table: _rows(table) for table in before} == before


def test_upgrade_base_vierge_initialise_schema_actuel_et_head(migration_app):
    assert sa.inspect(db.engine).get_table_names() == []

    _success(_cli(migration_app, "upgrade"))

    assert _revision() == REVISION
    inspector = sa.inspect(db.engine)
    assert set(CIVIL_COLUMNS) <= {column["name"] for column in inspector.get_columns("eleve")}
    assert SNAPSHOT_COLUMNS <= {column["name"] for column in inspector.get_columns("certificat_administratif")}
    assert any(index["unique"] and index["column_names"] == ["idempotency_key"]
               for index in inspector.get_indexes("paiement"))
    _success(_cli(migration_app, "upgrade"))


def test_downgrade_refuse_sans_perdre_identites_paiements_ou_snapshots(migration_app):
    _legacy_schema(migration_app, certificate=True)
    _success(_cli(migration_app, "upgrade"))
    with db.engine.begin() as connection:
        connection.execute(sa.text("UPDATE certificat_administratif SET nom_eleve = 'Nom scellé' WHERE id = 1"))
    schema_before = _schema_snapshot()
    before = {table: _rows(table) for table in ("eleve", "paiement", "certificat_administratif")}

    result = _cli(migration_app, "downgrade", PREVIOUS_REVISION)

    assert result.exit_code != 0
    assert result.exception is not None
    assert _revision() == REVISION
    assert _schema_snapshot() == schema_before
    assert {table: _rows(table) for table in before} == before


def test_upgrade_preserve_trigger_de_permanence_du_matricule(migration_app):
    _legacy_schema(migration_app)
    with db.engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TRIGGER matricule_permanent BEFORE UPDATE OF matricule ON eleve "
            "WHEN OLD.matricule IS NOT NULL AND NEW.matricule IS NOT OLD.matricule "
            "BEGIN SELECT RAISE(ABORT, 'matricule permanent'); END"
        ))

    _success(_cli(migration_app, "upgrade"))

    with pytest.raises(IntegrityError, match="matricule permanent"):
        with db.engine.begin() as connection:
            connection.execute(sa.text("UPDATE eleve SET matricule = '25-9999' WHERE id = 1"))
    with db.engine.begin() as connection:
        connection.execute(sa.text("UPDATE eleve SET nom = 'Nom corrigé' WHERE id = 1"))
    assert _rows("eleve")[0]["matricule"] == "25-0001"
    assert _rows("eleve")[0]["nom"] == "Nom corrigé"
