import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.admin import scripts as backup_scripts


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


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_DATABASE_URL"), "TEST_POSTGRES_DATABASE_URL non defini")
class BackupMultiBackendPostgreSQLTestCase(unittest.TestCase):
    def test_postgresql_backup_requires_real_vps_validation(self):
        self.skipTest("A lancer sur VPS avec pg_dump et TEST_POSTGRES_DATABASE_URL.")


if __name__ == "__main__":
    unittest.main()
