from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from doksio.project.url_helpers import build_public_url
from doksio.project.version import build_version


def test_upload_limits_are_sized_for_document_uploads():
    assert settings.DATA_UPLOAD_MAX_MEMORY_SIZE >= 100 * 1024 * 1024
    assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE <= settings.DATA_UPLOAD_MAX_MEMORY_SIZE


@override_settings(DOKSIO_PUBLIC_BASE_URL="https://doksio.example.test/app/")
def test_build_public_url_uses_configured_system_url():
    assert (
        build_public_url("/t/acme/import/api/v1/42/")
        == "https://doksio.example.test/app/t/acme/import/api/v1/42/"
    )


@override_settings(DOKSIO_PUBLIC_BASE_URL="localhost:8000")
def test_build_public_url_requires_absolute_http_url():
    with pytest.raises(ImproperlyConfigured):
        build_public_url("/t/acme/")


def test_portainer_stack_contains_production_services():
    stack_file = Path("deploy/portainer-stack.yml")
    content = stack_file.read_text()

    assert "gunicorn" in content
    assert "doksio.project.wsgi:application" in content
    assert "celery -A doksio.project worker" in content
    assert "CELERY_WORKER_CONCURRENCY: ${CELERY_WORKER_CONCURRENCY:-1}" in content
    assert (
        "CELERY_WORKER_PREFETCH_MULTIPLIER: "
        "${CELERY_WORKER_PREFETCH_MULTIPLIER:-1}"
    ) in content
    assert "celery -A doksio.project beat" in content
    assert "postgres:17-alpine" in content
    assert "redis:7-alpine" in content
    assert "minio/minio" in content
    assert "minio-init" in content


def test_build_version_uses_environment_value(monkeypatch):
    build_version.cache_clear()
    monkeypatch.setenv("DOKSIO_BUILD_VERSION", "20260713-1336")

    assert build_version() == "20260713-1336"

    build_version.cache_clear()


@override_settings(DOKSIO_BUILD_VERSION="")
def test_build_version_uses_build_metadata_file(tmp_path, monkeypatch):
    build_version.cache_clear()
    monkeypatch.delenv("DOKSIO_BUILD_VERSION", raising=False)
    metadata_file = tmp_path / ".doksio-build-version"
    metadata_file.write_text("20260713-1404\n", encoding="utf-8")

    with override_settings(BASE_DIR=tmp_path):
        assert build_version() == "20260713-1404"

    build_version.cache_clear()
