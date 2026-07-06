"""WSGI config for DoMaSy."""

from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "domasy.project.settings.local")

application = get_wsgi_application()
