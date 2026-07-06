"""Celery application for DoMaSy."""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "domasy.project.settings.local")

app = Celery("domasy")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
