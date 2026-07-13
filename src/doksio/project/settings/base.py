"""Base settings for Doksio."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[4]
SRC_DIR = BASE_DIR / "src"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def env_list(name: str, default: str = "") -> list[str]:
    raw_value = os.getenv(name, default)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-local-development-key")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", False)
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
if env_bool("DJANGO_SECURE_PROXY_SSL_HEADER", True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DOKSIO_PUBLIC_BASE_URL = os.getenv(
    "DOKSIO_PUBLIC_BASE_URL",
    "http://localhost:8000",
)
DOKSIO_BUILD_VERSION = os.getenv("DOKSIO_BUILD_VERSION", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django_htmx",
    "storages",
    "doksio.tenancy",
    "doksio.accounts",
    "doksio.audit",
    "doksio.documents",
    "doksio.storage",
    "doksio.ingestion",
    "doksio.ocr",
    "doksio.search",
    "doksio.workflows",
    "doksio.reports",
    "doksio.exports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "doksio.project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [SRC_DIR / "doksio" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "doksio.project.context_processors.doksio_version",
                "doksio.accounts.context_processors.user_profile",
            ],
        },
    },
]

WSGI_APPLICATION = "doksio.project.wsgi.application"
ASGI_APPLICATION = "doksio.project.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "doksio"),
        "USER": os.getenv("POSTGRES_USER", "doksio"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "doksio"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    SRC_DIR / "doksio" / "static",
]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DATA_UPLOAD_MAX_MEMORY_SIZE = env_int(
    "DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE",
    100 * 1024 * 1024,
)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int(
    "DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE",
    5 * 1024 * 1024,
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_BACKEND = os.getenv(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("DJANGO_EMAIL_PORT", 25)
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("DJANGO_EMAIL_USE_SSL", False)
DEFAULT_FROM_EMAIL = os.getenv(
    "DJANGO_DEFAULT_FROM_EMAIL",
    "Doksio <noreply@localhost>",
)

CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60

OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "deu+eng")
OCR_COMMAND_TIMEOUT_SECONDS = int(os.getenv("OCR_COMMAND_TIMEOUT_SECONDS", "300"))
OCR_TESSERACT_TIMEOUT_SECONDS = int(os.getenv("OCR_TESSERACT_TIMEOUT_SECONDS", "120"))
OCR_IMAGE_MAX_EDGE = int(os.getenv("OCR_IMAGE_MAX_EDGE", "3000"))
OCR_IMAGE_MAX_PAGES = int(os.getenv("OCR_IMAGE_MAX_PAGES", "25"))
OCR_IMAGE_ENHANCED_MAX_PAGES = int(os.getenv("OCR_IMAGE_ENHANCED_MAX_PAGES", "1"))
OCR_AUTO_START_ON_UPLOAD = env_bool("OCR_AUTO_START_ON_UPLOAD", True)
OCR_RUN_INLINE = env_bool("OCR_RUN_INLINE", DEBUG)

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

AWS_S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
AWS_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.getenv("S3_STORAGE_BUCKET_NAME", "doksio-documents")
AWS_S3_REGION_NAME = os.getenv("S3_REGION_NAME", "eu-central-1")
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None
