"""Настройки Data Room. Все значения экземпляра берутся из окружения (см. .env.example)."""
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG", False)

# --- Секреты ---------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-dev-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set.")

# --- Адрес экземпляра ------------------------------------------------------
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://localhost:8000" if DEBUG else None)
if not PUBLIC_BASE_URL:
    raise ImproperlyConfigured("PUBLIC_BASE_URL must be set.")
PUBLIC_BASE_URL = PUBLIC_BASE_URL.rstrip("/")
_parsed = urlparse(PUBLIC_BASE_URL)
if _parsed.scheme not in {"http", "https"} or not _parsed.hostname:
    raise ImproperlyConfigured("PUBLIC_BASE_URL must be an absolute http(s) URL.")
if _parsed.scheme != "https" and not DEBUG:
    raise ImproperlyConfigured("PUBLIC_BASE_URL must use https outside DEBUG.")
_HTTPS = _parsed.scheme == "https"

# Произвольный Host не принимается: только хост из PUBLIC_BASE_URL (+ явный список).
ALLOWED_HOSTS = [_parsed.hostname] + [h.strip() for h in env("ALLOWED_HOSTS_EXTRA", "").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [f"{_parsed.scheme}://{_parsed.netloc}"]

# Заголовки от proxy принимаем только если оператор включил доверие.
if env_bool("TRUST_PROXY_HEADERS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Бренд экземпляра ------------------------------------------------------
SITE_NAME = env("SITE_NAME", "Data Room")
LOGO = env("LOGO", "")
BRAND_COLOR = env("BRAND_COLOR", "#235244")
if not re.fullmatch(r"#[0-9a-fA-F]{6}", BRAND_COLOR):
    raise ImproperlyConfigured("BRAND_COLOR must look like #RRGGBB.")

# --- Приложения ------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.InstanceLocaleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dataroom.urls"
WSGI_APPLICATION = "dataroom.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.branding",
            ],
        },
    },
]

# --- База данных -----------------------------------------------------------
# В продакшене нужен PostgreSQL (DATABASE_URL). SQLite допустим только при DEBUG.
_db_url = env("DATABASE_URL")
if not _db_url:
    if DEBUG:
        _db_url = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    else:
        raise ImproperlyConfigured("DATABASE_URL must be set.")
DATABASES = {"default": dj_database_url.parse(_db_url, conn_max_age=60)}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Аутентификация --------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Сессии, cookie, заголовки безопасности --------------------------------
# Cookie без атрибута Domain: действуют только на хосте экземпляра.
SESSION_COOKIE_NAME = env("SESSION_COOKIE_NAME", "dataroom_session")
SESSION_COOKIE_SECURE = _HTTPS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_NAME = env("CSRF_COOKIE_NAME", "dataroom_csrf")
CSRF_COOKIE_SECURE = _HTTPS
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_REFERRER_POLICY = "no-referrer"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if _HTTPS:
    SECURE_HSTS_SECONDS = int(env("HSTS_SECONDS", "31536000"))

# --- Языки -----------------------------------------------------------------
# Допускаются только эти четыре кода (ТЗ, раздел 6).
LANGUAGES = [
    ("ru", "Русский"),
    ("uk", "Українська"),
    ("de", "Deutsch"),
    ("en", "English"),
]
SUPPORTED_LANGUAGE_CODES = [code for code, _ in LANGUAGES]
LANGUAGE_CODE = "en"
# Язык экземпляра. Если не задан или недопустим, используется английский.
DEFAULT_LOCALE = env("DEFAULT_LOCALE", "en")
if DEFAULT_LOCALE not in SUPPORTED_LANGUAGE_CODES:
    DEFAULT_LOCALE = "en"
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_COOKIE_NAME = "dataroom_lang"
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
LANGUAGE_COOKIE_SECURE = _HTTPS
LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SAMESITE = "Lax"

# --- Статика и приватные файлы ---------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# Приватный том документов. Не входит в STATIC/MEDIA и не раздаётся напрямую.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "private_media")))
MAX_UPLOAD_BYTES = int(env("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))

# --- Почта -----------------------------------------------------------------
EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend" if DEBUG and not env("EMAIL_HOST") else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", f"no-reply@{_parsed.hostname}")
