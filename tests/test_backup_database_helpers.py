"""Snapshots autonomes et alignement des séquences PostgreSQL."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.admin.backup_database import aligner_sequences_postgresql, lecture_snapshot


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'snapshot.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("CREATE TABLE snapshot_item (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO snapshot_item VALUES (1)")
    yield engine
    engine.dispose()


def test_snapshot_sqlite_reste_coherent_pendant_ecriture_concurrente(sqlite_engine):
    with lecture_snapshot(sqlite_engine) as reader:
        assert reader.execute(text("SELECT COUNT(*) FROM snapshot_item")).scalar() == 1
        assert reader.connection().connection.driver_connection.in_transaction
        with sqlite_engine.begin() as writer:
            writer.exec_driver_sql("INSERT INTO snapshot_item VALUES (2)")
        assert reader.execute(text("SELECT COUNT(*) FROM snapshot_item")).scalar() == 1
    with sqlite_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM snapshot_item").scalar() == 2


def test_snapshot_ne_committe_pas_transaction_metier(sqlite_engine):
    with Session(sqlite_engine) as business:
        business.execute(text("INSERT INTO snapshot_item VALUES (2)"))
        with lecture_snapshot(sqlite_engine) as reader:
            assert reader.execute(text("SELECT COUNT(*) FROM snapshot_item")).scalar() == 1
        assert business.in_transaction()
        assert business.execute(text("SELECT COUNT(*) FROM snapshot_item")).scalar() == 2
        business.rollback()
    with sqlite_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM snapshot_item").scalar() == 1


def test_snapshot_refuse_staticpool_occupe_sans_annuler_transaction():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE snapshot_item (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO snapshot_item VALUES (1)")
            with pytest.raises(RuntimeError, match="transaction en cours"):
                with lecture_snapshot(engine):
                    pass
            assert connection.connection.driver_connection.in_transaction
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM snapshot_item").scalar() == 1
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM snapshot_item").scalar() == 1
    finally:
        engine.dispose()


def test_snapshot_postgresql_lecture_seule_repeatable_read_sans_pragma():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execution_options.return_value = connection
    transaction = connection.begin.return_value
    transaction.is_active = True
    reader = MagicMock()
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"), connect=lambda: connection,
    )
    with patch("app.admin.backup_database.Session", return_value=reader):
        with lecture_snapshot(engine) as snapshot:
            assert snapshot is reader
    connection.execution_options.assert_called_once_with(isolation_level="REPEATABLE READ")
    assert str(connection.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    assert not connection.exec_driver_sql.called
    reader.close.assert_called_once()
    transaction.rollback.assert_called_once()


class PostgreSQLSequenceConnection:
    def __init__(self, maximum, last_value, is_called=True, sequence="public.student_id_seq"):
        self.dialect = postgresql.dialect()
        self.maximum = maximum
        self.last_value = last_value
        self.is_called = is_called
        self.sequence = sequence
        self.sql = []
        self.adjustments = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        if "pg_get_serial_sequence" in sql:
            return SimpleNamespace(scalar=lambda: self.sequence)
        if "FROM pg_class" in sql:
            return SimpleNamespace(one=lambda: ('school"schema', 'student"sequence'))
        if "setval" in sql:
            self.adjustments.append(params)
            return SimpleNamespace(scalar=lambda: self.maximum)
        raise AssertionError(sql)

    def exec_driver_sql(self, sql):
        self.sql.append(sql)
        if sql.startswith("LOCK TABLE"):
            return None
        if sql.startswith("SELECT MAX"):
            return SimpleNamespace(scalar=lambda: self.maximum)
        if sql.startswith("SELECT last_value"):
            return SimpleNamespace(one=lambda: (self.last_value, self.is_called))
        raise AssertionError(sql)


def _student_table():
    return Table(
        'student"ledger', MetaData(), Column("id", Integer, primary_key=True),
        schema='school"schema',
    )


@pytest.mark.parametrize("maximum,last_value,is_called,adjusted", [
    (50, 3, True, True),
    (5, 80, True, False),
    (8, 8, True, False),
    (1, 1, False, True),
    (None, 80, True, False),
])
def test_sequence_utilise_max_global_sans_recul(maximum, last_value, is_called, adjusted):
    connection = PostgreSQLSequenceConnection(maximum, last_value, is_called)
    aligner_sequences_postgresql(connection, [_student_table()])
    assert bool(connection.adjustments) is adjusted
    assert not any("PRAGMA" in sql for sql in connection.sql)
    assert 'LOCK TABLE "school""schema"."student""ledger" IN SHARE ROW EXCLUSIVE MODE' in connection.sql
    if adjusted:
        assert connection.adjustments[0]["maximum"] == maximum
        assert 'SELECT last_value, is_called FROM "school""schema"."student""sequence"' in connection.sql


def test_sequence_ignore_sqlite_et_cles_non_entieres():
    sqlite = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    aligner_sequences_postgresql(sqlite, [_student_table()])
    connection = PostgreSQLSequenceConnection(5, 1)
    table = Table("token", MetaData(), Column("id", String, primary_key=True))
    aligner_sequences_postgresql(connection, [table])
    assert connection.sql == []


def test_sequence_ignore_colonne_sans_sequence():
    connection = PostgreSQLSequenceConnection(5, 1, sequence=None)
    aligner_sequences_postgresql(connection, [_student_table()])
    assert len(connection.sql) == 1
    assert not connection.adjustments
