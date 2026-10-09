"""Garantie d'unicité sur les bases anciennes, sans toucher à une base d'école."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.config import TestingConfig
from scripts.migrate_caisse_idempotence_et_certificats import migrate


class MigrationTestConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': StaticPool, 'connect_args': {'check_same_thread': False},
    }


def test_migration_rend_la_cle_unique_sur_un_ancien_schema():
    app = create_app(MigrationTestConfig)
    with app.app_context():
        assert str(db.engine.url) == 'sqlite:///:memory:'
        with db.engine.begin() as connection:
            connection.execute(text('CREATE TABLE paiement (id INTEGER PRIMARY KEY, idempotency_key VARCHAR(64))'))
            connection.execute(text('CREATE INDEX ix_paiement_idempotency_key ON paiement (idempotency_key)'))
            connection.execute(text("INSERT INTO paiement (id, idempotency_key) VALUES (1, 'operation-a')"))

        migrate(app)
        assert any(index['unique'] and index['column_names'] == ['idempotency_key']
                   for index in inspect(db.engine).get_indexes('paiement'))
        with pytest.raises(Exception):
            with db.engine.begin() as connection:
                connection.execute(text("INSERT INTO paiement (id, idempotency_key) VALUES (2, 'operation-a')"))
