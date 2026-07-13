"""Local development settings."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import BASE_DIR

DEBUG = True
OCR_RUN_INLINE = True
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "*")  # noqa: F405

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
