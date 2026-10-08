"""Transactions de lecture et compteurs pour les sauvegardes multi-moteurs."""

from contextlib import contextmanager

from sqlalchemy import Integer, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.extensions import db


@contextmanager
def lecture_snapshot(engine=None):
    """Expose une session de lecture cohérente sans toucher à ``db.session``.

    Les requêtes doivent utiliser la session retournée pendant ce contexte.
    PostgreSQL utilise REPEATABLE READ, SQLite un BEGIN réel : son pilote ne
    démarre pas nécessairement une transaction pour les simples SELECT.
    """
    engine = engine if engine is not None else db.engine
    dialect = engine.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError(f"Moteur de sauvegarde non pris en charge : {dialect}.")

    # StaticPool (notamment SQLite :memory:) partage une seule connexion.
    # La fermer avec un rollback annulerait une transaction métier en cours.
    if dialect == "sqlite" and isinstance(engine.pool, StaticPool):
        record = engine.pool.__dict__.get("connection")
        driver = getattr(record, "dbapi_connection", None)
        if driver is not None and driver.in_transaction:
            raise RuntimeError(
                "La connexion SQLite partagée a une transaction en cours ; "
                "la sauvegarde nécessite une connexion de lecture indépendante."
            )

    with engine.connect() as connection:
        if dialect == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        transaction = connection.begin()
        reader = None
        try:
            if dialect == "sqlite":
                connection.exec_driver_sql("BEGIN")
            else:
                connection.execute(text("SET TRANSACTION READ ONLY"))
            reader = Session(bind=connection, autoflush=False, expire_on_commit=False)
            yield reader
        finally:
            if reader is not None:
                reader.close()
            if transaction.is_active:
                transaction.rollback()


def aligner_sequences_postgresql(connection, models):
    """Aligne les séquences après des insertions avec IDs explicites.

    À appeler dans la transaction de restauration, avant son commit. Les
    verrous de table empêchent un INSERT concurrent de dépasser le compteur
    entre sa lecture et son ajustement. Les noms viennent des métadonnées
    SQLAlchemy ou des catalogues PostgreSQL et sont toujours cités.
    """
    if connection.dialect.name != "postgresql":
        return

    tables = {}
    for model in models:
        table = getattr(model, "__table__", model)
        column = table.c.get("id")
        if (
            column is not None
            and column.primary_key
            and isinstance(column.type, Integer)
            and len(table.primary_key.columns) == 1
        ):
            tables[table.fullname] = table

    preparer = connection.dialect.identifier_preparer
    for table in sorted(tables.values(), key=lambda item: item.fullname):
        table_sql = preparer.format_table(table)
        sequence = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table_sql, "column_name": "id"},
        ).scalar()
        if not sequence:
            continue

        connection.exec_driver_sql(f"LOCK TABLE {table_sql} IN SHARE ROW EXCLUSIVE MODE")
        maximum = connection.exec_driver_sql(
            f"SELECT MAX({preparer.quote('id')}) FROM {table_sql}"
        ).scalar()
        if maximum is None:
            continue

        schema, name = connection.execute(
            text(
                "SELECT n.nspname, c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.oid = CAST(:sequence AS regclass)"
            ),
            {"sequence": sequence},
        ).one()
        sequence_sql = f"{preparer.quote_schema(schema)}.{preparer.quote(name)}"
        last_value, is_called = connection.exec_driver_sql(
            f"SELECT last_value, is_called FROM {sequence_sql}"
        ).one()
        if maximum > last_value or (maximum == last_value and not is_called):
            connection.execute(
                text("SELECT setval(CAST(:sequence AS regclass), :maximum, true)"),
                {"sequence": sequence, "maximum": maximum},
            )
