import os
import unittest
from unittest.mock import patch

from app.config import (
    Config,
    DevelopmentConfig,
    ProductionConfig,
    TestingConfig,
    get_config,
    is_production_environment,
    is_testing_environment,
)
from app import create_app


class DatabaseFailFastConfigTestCase(unittest.TestCase):
    def test_production_fails_fast_without_database_url(self):
        """En production sans DATABASE_URL, ProductionConfig.validate() doit lever RuntimeError."""
        with patch.dict(os.environ, {"DATABASE_URL": "", "APP_ENV": "production", "SECRET_KEY": "a" * 32}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                ProductionConfig.validate()
            self.assertIn("DATABASE_URL (PostgreSQL) est obligatoire en production", str(ctx.exception))
            self.assertIn("Le repli sur SQLite est formellement interdit", str(ctx.exception))

    def test_production_fails_fast_with_sqlite_url(self):
        """En production avec une URL SQLite, ProductionConfig.validate() doit lever RuntimeError."""
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///instance/ecole.db", "APP_ENV": "production", "SECRET_KEY": "a" * 32}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                ProductionConfig.validate()
            self.assertIn("DATABASE_URL (PostgreSQL) est obligatoire en production", str(ctx.exception))

    def test_production_validates_with_postgres_url(self):
        """En production avec une URL PostgreSQL valide, ProductionConfig.validate() doit réussir."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://user:pass@localhost:5432/klasora_db",
                "APP_ENV": "production",
                "SECRET_KEY": "a" * 32,
            },
            clear=False,
        ):
            self.assertTrue(ProductionConfig.validate())

    def test_development_allows_sqlite_fallback(self):
        """En développement, DevelopmentConfig.validate() doit réussir même sans DATABASE_URL."""
        with patch.dict(os.environ, {"DATABASE_URL": "", "APP_ENV": "development"}, clear=False):
            self.assertTrue(DevelopmentConfig.validate())

    def test_testing_allows_sqlite_memory(self):
        """En mode test, TestingConfig.validate() doit réussir avec SQLite in-memory."""
        with patch.dict(os.environ, {"DATABASE_URL": "", "APP_ENV": "testing"}, clear=False):
            self.assertTrue(TestingConfig.validate())

    def test_create_app_fails_fast_in_production_with_sqlite(self):
        """create_app(ProductionConfig) avec SQLite doit lever un RuntimeError explicite."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite:///ecole.db",
                "APP_ENV": "production",
                "FLASK_ENV": "production",
                "SECRET_KEY": "a" * 32,
                "TESTING": "0",
            },
            clear=False,
        ):
            with patch("app.config.is_testing_environment", return_value=False):
                with self.assertRaises(RuntimeError) as ctx:
                    create_app(ProductionConfig)
                self.assertIn("DATABASE_URL (PostgreSQL) est obligatoire en production", str(ctx.exception))

