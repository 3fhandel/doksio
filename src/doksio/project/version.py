from __future__ import annotations

import os
import subprocess
from functools import lru_cache

from django.conf import settings


@lru_cache
def build_version() -> str:
    configured_version = (
        getattr(settings, "DOKSIO_BUILD_VERSION", "")
        or os.getenv("DOKSIO_BUILD_VERSION", "")
    ).strip()
    if configured_version:
        return configured_version

    build_version_file = settings.BASE_DIR / ".doksio-build-version"
    try:
        file_version = build_version_file.read_text(encoding="utf-8").strip()
    except OSError:
        file_version = ""
    if file_version:
        return file_version

    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "-1",
                "--format=%cd",
                "--date=format:%Y%m%d-%H%M",
            ],
            cwd=settings.BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except Exception:
        return ""
    return result.stdout.strip()
