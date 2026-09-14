import os
import unittest

import pytest
from sqlalchemy import create_engine, text


def _postgres_url():
    return os.environ.get("TEST_POSTGRES_DATABASE_URL")


@unittest.skipUnless(_postgres_url(), "TEST_POSTGRES_DATABASE_URL non defini")
@pytest.mark.postgresql
class PostgreSQLIntegrationSkeletonTestCase(unittest.TestCase):
    """Tests a lancer sur VPS avec une base PostgreSQL jetable."""

    def test_postgresql_select_1(self):
        engine = create_engine(_postgres_url(), pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT 1")).scalar(), 1)
        finally:
            engine.dispose()

    def test_migrations_and_init_system_manual_steps_documented(self):
        self.skipTest(
            "Sur VPS: set DATABASE_URL=TEST_POSTGRES_DATABASE_URL, "
            "puis flask db upgrade, flask init-system deux fois, "
            "verifier 13 niveaux et super admin."
        )


if __name__ == "__main__":
    unittest.main()
