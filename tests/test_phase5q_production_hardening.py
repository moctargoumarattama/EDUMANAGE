import os
import unittest
from unittest.mock import patch

from flask import request
from sqlalchemy.pool import StaticPool

from app import create_app, db
from app.config import DevelopmentConfig, ProductionConfig
from app.routes.common import limiter


class TestConfig:
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False}
    }
    SECRET_KEY = "test-phase5q"
    SERVER_NAME = None
    PROPAGATE_EXCEPTIONS = False
    LOGIN_DISABLED = False
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    RATELIMIT_STORAGE_URI = "memory://"
    USE_PROXY_FIX = False


class TinyUploadConfig(TestConfig):
    MAX_CONTENT_LENGTH = 8


class Phase5QProductionHardeningTestCase(unittest.TestCase):

    def create_test_app(self, config_class=TestConfig):
        app = create_app(config_class)
        self.addCleanup(lambda: None)
        return app

    def test_production_config_disables_debug_and_testing(self):
        self.assertFalse(ProductionConfig.DEBUG)
        self.assertFalse(ProductionConfig.TESTING)

    def test_production_config_uses_secure_cookies(self):
        self.assertTrue(ProductionConfig.SESSION_COOKIE_SECURE)
        self.assertTrue(ProductionConfig.REMEMBER_COOKIE_SECURE)
        self.assertTrue(ProductionConfig.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(ProductionConfig.SESSION_COOKIE_SAMESITE, "Lax")

    def test_development_config_keeps_local_http_usable(self):
        self.assertTrue(DevelopmentConfig.DEBUG)
        self.assertFalse(DevelopmentConfig.SESSION_COOKIE_SECURE)
        self.assertFalse(DevelopmentConfig.REMEMBER_COOKIE_SECURE)

    def test_large_upload_returns_413(self):
        app = self.create_test_app(TinyUploadConfig)

        @app.route("/_phase5q_upload", methods=["POST"])
        def phase5q_upload():
            request.get_data()
            return "ok"

        with app.app_context():
            db.create_all()

        response = app.test_client().post(
            "/_phase5q_upload",
            data=b"0123456789",
            content_type="application/octet-stream"
        )
        self.assertEqual(response.status_code, 413)

    def test_health_endpoint_returns_ok_when_database_responds(self):
        app = self.create_test_app()
        with app.app_context():
            db.create_all()

        response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["database"], "ok")

    def test_health_endpoint_returns_503_when_database_fails(self):
        app = self.create_test_app()
        with patch("app.routes.health.db.session.execute", side_effect=RuntimeError("db down")):
            response = app.test_client().get("/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["database"], "unavailable")

    def test_redis_absent_keeps_memory_fallback(self):
        self.assertEqual(os.environ.get("REDIS_URL") or "memory://", "memory://")
        self.assertIn("memory://", limiter._storage_uri)

    def test_login_route_still_loads_with_rate_limiter(self):
        app = self.create_test_app()
        with app.app_context():
            db.create_all()

        response = app.test_client().get("/login")
        self.assertNotEqual(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()
