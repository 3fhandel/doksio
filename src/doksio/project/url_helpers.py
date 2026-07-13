from __future__ import annotations

from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def build_public_url(path: str) -> str:
    base_url = getattr(settings, "DOKSIO_PUBLIC_BASE_URL", "").strip()
    parsed_base_url = urlparse(base_url)
    if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        raise ImproperlyConfigured(
            "DOKSIO_PUBLIC_BASE_URL must be an absolute http(s) URL.",
        )
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))

