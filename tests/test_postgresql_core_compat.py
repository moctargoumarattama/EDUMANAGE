import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr

from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.config import get_database_dialect, get_engine_options, normalize_database_url
from app.models import Utilisateur


class BlankSQLiteConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_ENGINE_OPTIONS = None
    SECRET_KEY = "test-postgresql-core"
    LOGIN_DISABLED = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    USE_PROXY_FIX = False


class PostgreSQLCoreCompatibilityTestCase(unittest.TestCase):

    def test_sqlite_engine_options_keep_timeout(self):
        options = get_engine_options("sqlite:///ecole.db")
        self.assertEqual(options, {"connect_args": {"timeout": 30}})

    def test_postgresql_engine_options_do_not_include_sqlite_timeout(self):
        options = get_engine_options("postgresql+psycopg://user:pass@localhost/db")
        self.assertTrue(options["pool_pre_ping"])
        self.assertEqual(options["pool_size"], 5)
        self.assertEqual(options["max_overflow"], 5)
        self.assertNotIn("connect_args", options)

    def test_legacy_postgres_url_is_normalized_for_psycopg(self):
        normalized = normalize_database_url("postgres://user:pass@localhost/db")
        self.assertEqual(normalized, "postgresql+psycopg://user:pass@localhost/db")
        self.assertEqual(get_database_dialect(normalized), "postgresql")

    def test_create_app_on_blank_sqlite_does_not_query_missing_user_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "blank.db")
            BlankSQLiteConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
            BlankSQLiteConfig.SQLALCHEMY_ENGINE_OPTIONS = {
                "poolclass": StaticPool,
                "connect_args": {"check_same_thread": False},
            }
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                app = create_app(BlankSQLiteConfig)
            self.assertIsNotNone(app)
            self.assertNotIn("no such table: utilisateur", stderr.getvalue())
            with app.app_context():
                db.engine.dispose()

    def test_create_app_with_schema_does_not_create_superadmin_until_init_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "schema.db")
            BlankSQLiteConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
            BlankSQLiteConfig.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
            app = create_app(BlankSQLiteConfig)
            try:
                with app.app_context():
                    db.create_all()
                    self.assertEqual(Utilisateur.query.count(), 0)

                read_result = app.test_cli_runner().invoke(args=["system-health"])
                self.assertEqual(read_result.exit_code, 0)
                with app.app_context():
                    self.assertEqual(Utilisateur.query.count(), 0)

                first = app.test_cli_runner().invoke(args=["init-system", "--quiet"])
                self.assertEqual(first.exit_code, 0)
                with app.app_context():
                    self.assertEqual(Utilisateur.query.filter_by(role="super_admin").count(), 1)

                second = app.test_cli_runner().invoke(args=["init-system", "--quiet"])
                self.assertEqual(second.exit_code, 0)
                with app.app_context():
                    self.assertEqual(Utilisateur.query.filter_by(role="super_admin").count(), 1)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_system_health_sqlite_reports_sqlite_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "health.db")
            BlankSQLiteConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
            BlankSQLiteConfig.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
            app = create_app(BlankSQLiteConfig)
            result = app.test_cli_runner().invoke(args=["system-health"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("sqlite_size_mb=", result.output)
            with app.app_context():
                db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
