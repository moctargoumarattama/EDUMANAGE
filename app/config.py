import os
from urllib.parse import urlsplit


class ConfigError(RuntimeError):
    pass


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(url):
    """Return a SQLAlchemy-compatible database URL without rebuilding secrets."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_database_dialect(url):
    return urlsplit(url).scheme.split("+", 1)[0] or "sqlite"


def get_engine_options(database_url):
    dialect = get_database_dialect(database_url)
    if dialect == "sqlite":
        return {"connect_args": {"timeout": 30}}
    if dialect == "postgresql":
        return {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            "pool_size": int(os.environ.get("DB_POOL_SIZE", "5")),
            "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "5")),
        }
    return {"pool_pre_ping": True}


class Config:
    """Configuration commune de l'application Flask."""

    APP_ENV = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    DEBUG = False
    TESTING = False

    # Securite
    SECRET_KEY = os.environ.get("SECRET_KEY", "ma_cle_ultra_secrete")
    SECURITY_PASSWORD_SALT = os.environ.get("SECURITY_PASSWORD_SALT", "mon_salt_securise")
    BULLETIN_VERIFICATION_KEY = os.environ.get("BULLETIN_VERIFICATION_KEY", "klasora_bulletin_verification_secret_key_stable")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Base de donnees
    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.environ.get("DATABASE_URL", "sqlite:///ecole.db"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = get_engine_options(SQLALCHEMY_DATABASE_URI)

    # Protection anti-abus
    RATELIMIT_HEADERS_ENABLED = True
    REDIS_URL = os.environ.get("REDIS_URL")
    RATELIMIT_STORAGE_URI = REDIS_URL or "memory://"

    # Cache optionnel. Aucune donnee metier sensible n'est cachee par defaut.
    CACHE_TYPE = "RedisCache" if REDIS_URL else "SimpleCache"
    CACHE_REDIS_URL = REDIS_URL
    CACHE_DEFAULT_TIMEOUT = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", "300"))

    # Uploads
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 20 * 1024 * 1024))

    # Flask-Mail conserve pour compatibilite, hors perimetre de cette phase.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.mail.me.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_USE_SSL = _env_bool("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)
    MAIL_DEBUG = os.environ.get("MAIL_DEBUG", "False").lower() in ("true", "1", "yes")
    # Support KLASORA
    SUPPORT_WHATSAPP_NUMBER = os.environ.get("SUPPORT_WHATSAPP_NUMBER", "212770010264")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "moctargoumarattama@gmail.com")

    # Divers
    VERSION = "2.2.0"
    SEND_FILE_MAX_AGE_DEFAULT = 2592000
    USE_PROXY_FIX = False

    @classmethod
    def validate(cls):
        return True


class DevelopmentConfig(Config):
    APP_ENV = "development"
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


class ProductionConfig(Config):
    APP_ENV = "production"
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    USE_PROXY_FIX = True

    @classmethod
    def validate(cls):
        weak_values = {
            "",
            "ma_cle_ultra_secrete",
            "change-me",
            "changer-cette-cle-secrete-en-production",
        }
        secret = os.environ.get("SECRET_KEY", "")
        if secret in weak_values or len(secret) < 32:
            raise ConfigError("SECRET_KEY de production absente ou trop faible.")
        return True


class TestingConfig(Config):
    APP_ENV = "testing"
    TESTING = True
    DEBUG = False
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


def get_config():
    env = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    if env in {"prod", "production"}:
        return ProductionConfig
    if env in {"test", "testing"}:
        return TestingConfig
    return DevelopmentConfig
