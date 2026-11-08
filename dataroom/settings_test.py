"""Настройки для тестов: SQLite, без внешних сервисов."""
import os

os.environ.setdefault("DJANGO_DEBUG", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")
os.environ.pop("DATABASE_URL", None)

from .settings import *  # noqa: E402,F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
