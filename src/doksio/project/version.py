from __future__ import annotations

import os
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.utils.dateparse import parse_datetime

BUILD_METADATA_DIR = Path("/opt/doksio")


def _metadata_value(filename: str) -> str:
    for directory in (settings.BASE_DIR, BUILD_METADATA_DIR):
        try:
            value = (directory / filename).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def _git_value(format_value: str) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "-1", f"--format={format_value}"],
            cwd=settings.BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except Exception:
        return ""
    return result.stdout.strip()


@lru_cache
def build_version() -> str:
    configured_version = (
        getattr(settings, "DOKSIO_BUILD_VERSION", "")
        or os.getenv("DOKSIO_BUILD_VERSION", "")
    ).strip()
    if configured_version:
        return configured_version

    file_version = _metadata_value(".doksio-build-version")
    if file_version:
        return file_version

    git_timestamp = _git_value("%cI")
    parsed_timestamp = parse_datetime(git_timestamp)
    if parsed_timestamp is not None:
        return parsed_timestamp.strftime("%Y%m%d-%H%M")
    return "Entwicklung"


@lru_cache
def build_datetime() -> datetime | None:
    metadata_timestamp = _metadata_value(".doksio-build-datetime")
    parsed_timestamp = parse_datetime(metadata_timestamp)
    if parsed_timestamp is not None:
        return parsed_timestamp

    git_timestamp = _git_value("%cI")
    return parse_datetime(git_timestamp)
