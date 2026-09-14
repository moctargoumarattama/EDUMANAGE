import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy import text
from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.admin import scripts as backup_scripts
from app.models import Ecole


class BackupTestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_ENGINE_OPTIONS = None
    SECRET_KEY = "test-backup-multi-backend"
    LOGIN_DISABLED = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    USE_PROXY_FIX = False


class BackupMultiBackendSQLiteTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.backup_dir = os.path.join(self.tmp.name, "backups")
        os.makedirs(self.backup_dir, exist_ok=True)
        self.db_path = os.path.join(self.tmp.name, "ecole.db")
        BackupTestConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.db_path}"
        BackupTestConfig.SQLALCHEMY_ENGINE_OPTIONS = {
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        }
        self.app = create_app(BackupTestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.remove()
        db.engine.dispose()
        self.old_backup_dir = backup_scripts.BACKUP_DIR
        self.old_db_path = backup_scripts.DB_PATH
        backup_scripts.BACKUP_DIR = self.backup_dir
        backup_scripts.DB_PATH = self.db_path

    def tearDown(self):
        backup_scripts.BACKUP_DIR = self.old_backup_dir
        backup_scripts.DB_PATH = self.old_db_path
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp.cleanup()

    def test_sqlite_global_backup_creates_db_file(self):
        backup_file = backup_scripts.create_backup()
        self.assertTrue(os.path.exists(backup_file))
        self.assertTrue(backup_file.endswith(".db"))
        self.assertGreater(os.path.getsize(backup_file), 0)
        self.assertEqual(len(backup_scripts._sha256_file(backup_file)), 64)

    def test_sqlite_global_backup_rotation_keeps_configured_count(self):
        with patch.dict(os.environ, {"POSTGRES_BACKUP_RETENTION": "2"}):
            for _ in range(4):
                backup_scripts.create_backup()
        backups = [f for f in os.listdir(self.backup_dir) if f.startswith("backup_") and f.endswith(".db")]
        self.assertLessEqual(len(backups), 2)


class AdminLogActionBestEffortTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "log.db")
        BackupTestConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.db_path}"
        BackupTestConfig.SQLALCHEMY_ENGINE_OPTIONS = {
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        }
        self.app = create_app(BackupTestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp.cleanup()

    def test_log_action_rolls_back_when_log_commit_fails(self):
        with patch.object(db.session, "commit", side_effect=RuntimeError("log commit failed")):
            with patch.object(db.session, "rollback", wraps=db.session.rollback) as rollback:
                backup_scripts.log_action("TEST", "commit impossible")
                self.assertTrue(rollback.called)

        ecole = Ecole(nom="Ecole apres rollback")
        db.session.add(ecole)
        db.session.commit()
        self.assertEqual(Ecole.query.count(), 1)

    def test_log_action_table_absent_does_not_poison_session(self):
        db.session.execute(text("DROP TABLE log"))
        db.session.commit()

        backup_scripts.log_action("TEST", "table log absente")

        ecole = Ecole(nom="Ecole apres table absente")
        db.session.add(ecole)
        db.session.commit()
        self.assertEqual(Ecole.query.count(), 1)


class BackupMultiBackendPostgreSQLTestCase(unittest.TestCase):
    def _fake_postgresql_engine(self, url):
        return SimpleNamespace(
            url=make_url(url),
            dialect=SimpleNamespace(name="postgresql"),
        )

    def test_postgresql_backend_keeps_real_password_only_in_env(self):
        # This test protects against str(db.engine.url), which redacts passwords.
        special_password = "p@ss:w/or?d#2026"
        engine = self._fake_postgresql_engine(
            "postgresql+psycopg://klasora_app:p%40ss%3Aw%2For%3Fd%232026@127.0.0.1:5432/klasora"
        )
        with patch.object(backup_scripts, "db", SimpleNamespace(engine=engine)):
            backend = backup_scripts.PostgreSQLBackupBackend()
            self.assertEqual(backend._pg_env()["PGPASSWORD"], special_password)
            args = backend._connection_args()
            self.assertNotIn(special_password, args)
            self.assertNotIn(engine.url.render_as_string(hide_password=False), args)

    def test_postgresql_backend_detection(self):
        engine = self._fake_postgresql_engine("postgresql+psycopg://user:pass@127.0.0.1:5432/klasora")
        with patch.object(backup_scripts, "db", SimpleNamespace(engine=engine)):
            backend = backup_scripts.get_database_backup_backend()
            self.assertIsInstance(backend, backup_scripts.PostgreSQLBackupBackend)


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_DATABASE_URL"), "TEST_POSTGRES_DATABASE_URL non defini")
class BackupMultiBackendPostgreSQLRealTestCase(unittest.TestCase):
    def test_postgresql_backend_detection_with_real_url(self):
        engine = create_engine(os.environ["TEST_POSTGRES_DATABASE_URL"])
        try:
            with patch.object(backup_scripts, "db", SimpleNamespace(engine=engine)):
                backend = backup_scripts.get_database_backup_backend()
                self.assertIsInstance(backend, backup_scripts.PostgreSQLBackupBackend)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
