from pathlib import Path
import os
from datetime import timedelta
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def load_local_env(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name:
            os.environ.setdefault(name, value.strip().strip('"').strip("'"))


load_local_env(BASE_DIR.parent / ".env")


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def get_secret_key():
    key = os.getenv("DJANGO_SECRET_KEY") or os.getenv("SECRET_KEY")
    if not key:
        if env_bool("DEBUG"):
            return "django-insecure-development-only"
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG=False.")
    return key


SECRET_KEY = get_secret_key()
DEBUG = env_bool("DEBUG")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT in {"production", "prod"}
if IS_PRODUCTION and DEBUG:
    raise ImproperlyConfigured("DEBUG must be False in production.")
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")
CI_COMPOSE_VALIDATION = env_bool("CI_COMPOSE_VALIDATION")
if IS_PRODUCTION and not CI_COMPOSE_VALIDATION and any(host in {"*", "localhost", "127.0.0.1", "::1"} for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Production ALLOWED_HOSTS cannot contain wildcard or loopback hosts.")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
if IS_PRODUCTION and ("*" in ALLOWED_HOSTS or not ALLOWED_HOSTS):
    raise ImproperlyConfigured("Production ALLOWED_HOSTS must explicitly list trusted hosts.")
_frontend_parts = urlparse(FRONTEND_URL)
if _frontend_parts.scheme not in {"http", "https"} or not _frontend_parts.netloc:
    raise ImproperlyConfigured("FRONTEND_URL must be an absolute http(s) URL.")
EMAIL_VERIFICATION_REQUIRED = env_bool("EMAIL_VERIFICATION_REQUIRED", not DEBUG)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Developer OS <no-reply@localhost>")
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend" if os.getenv("EMAIL_HOST") else "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL")
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
CACHE_URL = os.getenv("CACHE_URL", "")

# AI gateway configuration. Never commit the API key.
AI_API_KEY = (os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY") or "").strip()
AI_API_URL = (os.getenv("AI_API_URL") or "https://api.openai.com/v1").strip().rstrip("/")
AI_MODEL = (os.getenv("AI_MODEL") or "gpt-6-luna").strip()
AI_API_PROTOCOL = (os.getenv("AI_API_PROTOCOL") or "responses").strip().lower()
AI_API_KEY_CONFIGURED = bool(AI_API_KEY)
ALLOW_LOCAL_BILLING = env_bool("ALLOW_LOCAL_BILLING")

if IS_PRODUCTION and EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
    raise ImproperlyConfigured("Console email backend is forbidden in production.")

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_TOKEN_ENCRYPTION_KEY = os.getenv("GITHUB_TOKEN_ENCRYPTION_KEY", "")
GITHUB_OAUTH_REDIRECT = os.getenv("GITHUB_OAUTH_REDIRECT", f"{FRONTEND_URL}/api/github/callback/").strip()
if IS_PRODUCTION and GITHUB_OAUTH_REDIRECT:
    _oauth_redirect = urlparse(GITHUB_OAUTH_REDIRECT)
    if _oauth_redirect.scheme != "https" or not _oauth_redirect.netloc:
        raise ImproperlyConfigured("Production GITHUB_OAUTH_REDIRECT must be an absolute HTTPS URL.")
if IS_PRODUCTION and bool(GITHUB_CLIENT_ID) != bool(GITHUB_CLIENT_SECRET):
    raise ImproperlyConfigured("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be configured together.")

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "rest_framework_simplejwt.token_blacklist", "api", "corsheaders", "channels"
]

MIDDLEWARE = [
    "api.middleware.RequestObservabilityMiddleware", "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "api.security.ApiRateLimitMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware", "api.middleware.ApiSecurityHeadersMiddleware",
]

ROOT_URLCONF = "x.urls"
ASGI_APPLICATION = "x.asgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "x.wsgi.application"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_POOL_ENABLED = env_bool("DB_POOL_ENABLED", IS_PRODUCTION)
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "4"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "10"))
# Keep the per-process pool deliberately bounded. With horizontal web/worker
# scaling, an accidental large value multiplies directly into PostgreSQL
# connection pressure. Operators can still scale the service horizontally
# without allowing one process to monopolize the database.
if DB_POOL_MIN_SIZE < 0 or DB_POOL_MAX_SIZE < max(1, DB_POOL_MIN_SIZE) or DB_POOL_MAX_SIZE > 32:
    raise ImproperlyConfigured("DB_POOL_MIN_SIZE/DB_POOL_MAX_SIZE are invalid; DB_POOL_MAX_SIZE must be between 1 and 32.")
if not 0 < DB_POOL_TIMEOUT <= 60:
    raise ImproperlyConfigured("DB_POOL_TIMEOUT must be greater than 0 and no more than 60 seconds.")
if DATABASE_URL:
    parsed = urlparse(DATABASE_URL)
    if parsed.scheme.startswith("postgres"):
        db_config = {"ENGINE": "django.db.backends.postgresql", "NAME": parsed.path.lstrip("/"), "USER": parsed.username or "", "PASSWORD": parsed.password or "", "HOST": parsed.hostname or "", "PORT": str(parsed.port or 5432), "CONN_HEALTH_CHECKS": True, "DISABLE_SERVER_SIDE_CURSORS": True}
        if DB_POOL_ENABLED:
            db_config["CONN_MAX_AGE"] = 0
            db_config["OPTIONS"] = {"pool": {"min_size": DB_POOL_MIN_SIZE, "max_size": DB_POOL_MAX_SIZE, "timeout": DB_POOL_TIMEOUT}}
        else:
            db_config["CONN_MAX_AGE"] = int(os.getenv("DB_CONN_MAX_AGE", "60"))
        DATABASES = {"default": db_config}
    else:
        raise ImproperlyConfigured("DATABASE_URL must be a PostgreSQL URL.")
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
if IS_PRODUCTION and not DATABASE_URL:
    raise ImproperlyConfigured("DATABASE_URL is required in production.")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "api.avatar_storage.ProfileMediaStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
    SECURE_CROSS_ORIGIN_RESOURCE_POLICY = "same-origin"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", ",".join([FRONTEND_URL, "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"]))
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", FRONTEND_URL if FRONTEND_URL.startswith(("http://", "https://")) else "")
if IS_PRODUCTION:
    def _validate_production_origins(name, origins):
        if not origins:
            raise ImproperlyConfigured(f"Production {name} must contain explicit trusted origins.")
        for origin in origins:
            parsed = urlparse(origin)
            if origin == "*" or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path not in {"", "/"}:
                raise ImproperlyConfigured(f"Production {name} contains an unsafe origin: {origin!r}.")
            if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
                raise ImproperlyConfigured(f"Production {name} cannot contain loopback hosts.")
    _validate_production_origins("CORS_ALLOWED_ORIGINS", CORS_ALLOWED_ORIGINS)
    _validate_production_origins("CSRF_TRUSTED_ORIGINS", CSRF_TRUSTED_ORIGINS)
CORS_ALLOW_CREDENTIALS = False
TRUST_PROXY_HEADERS = env_bool("TRUST_PROXY_HEADERS")
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_EMBEDDER_POLICY = "require-corp" if not DEBUG else None
DATA_UPLOAD_MAX_MEMORY_SIZE = 6_291_456
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440

if IS_PRODUCTION and not CACHE_URL:
    raise ImproperlyConfigured("CACHE_URL is required in production.")
if CACHE_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": CACHE_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "developer-os"}}

if CACHE_URL:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [CACHE_URL]}}}
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

REST_FRAMEWORK = {"EXCEPTION_HANDLER": "api.security.api_exception_handler", "DEFAULT_AUTHENTICATION_CLASSES": ("api.authentication.APIKeyAuthentication", "rest_framework_simplejwt.authentication.JWTAuthentication"), "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",), "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.AnonRateThrottle", "rest_framework.throttling.UserRateThrottle"), "DEFAULT_THROTTLE_RATES": {"anon": "60/hour", "user": "2000/day", "auth": "10/hour", "email_verification": "5/hour", "password_reset": "5/hour", "assistant": "30/hour", "referral": "12/hour", "runner": "20/minute"}}
SIMPLE_JWT = {"ACCESS_TOKEN_LIFETIME": timedelta(minutes=5), "REFRESH_TOKEN_LIFETIME": timedelta(days=1), "ROTATE_REFRESH_TOKENS": True, "BLACKLIST_AFTER_ROTATION": True, "AUTH_HEADER_TYPES": ("Bearer",), "UPDATE_LAST_LOGIN": False, "LEEWAY": 5}
LOGGING = {"version": 1, "disable_existing_loggers": False, "formatters": {"json": {"()": "api.logging.JsonLogFormatter"}}, "handlers": {"console_json": {"class": "logging.StreamHandler", "formatter": "json"}}, "loggers": {"developer_os": {"handlers": ["console_json"], "level": os.getenv("LOG_LEVEL", "INFO"), "propagate": False}}}