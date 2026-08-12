from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from doksio.project.changelog import rendered_changelog
from doksio.project.url_helpers import build_public_url
from doksio.project.version import build_datetime, build_version


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

    dockerfile = Path("Dockerfile").read_text()
    assert "/opt/doksio/.doksio-build-version" in dockerfile
    assert "/opt/doksio/.doksio-build-datetime" in dockerfile
    assert "COPY pyproject.toml README.md CHANGELOG.md ./" in dockerfile


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


@override_settings(DOKSIO_BUILD_VERSION="")
def test_build_version_has_visible_fallback_without_metadata_or_git(
    tmp_path,
    monkeypatch,
):
    build_version.cache_clear()
    monkeypatch.delenv("DOKSIO_BUILD_VERSION", raising=False)
    monkeypatch.setattr("doksio.project.version.BUILD_METADATA_DIR", tmp_path)
    monkeypatch.setattr("doksio.project.version._git_value", lambda _format: "")

    with override_settings(BASE_DIR=tmp_path):
        assert build_version() == "Entwicklung"

    build_version.cache_clear()


@override_settings(DOKSIO_BUILD_VERSION="")
def test_changelog_renders_build_metadata_and_german_markdown(tmp_path, monkeypatch):
    build_version.cache_clear()
    build_datetime.cache_clear()
    rendered_changelog.cache_clear()
    monkeypatch.delenv("DOKSIO_BUILD_VERSION", raising=False)
    monkeypatch.setattr("doksio.project.version.BUILD_METADATA_DIR", tmp_path / "opt")
    (tmp_path / ".doksio-build-version").write_text(
        "20260713-1404\n",
        encoding="utf-8",
    )
    (tmp_path / ".doksio-build-datetime").write_text(
        "2026-07-13T14:04:00+02:00\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Änderungen\n\n"
        "## Build {{ build_number }}\n\n"
        "**Datum/Uhrzeit:** {{ build_datetime }}\n\n"
        "### Neuerungen\n\n"
        "- Dokumente können zusammengeführt werden.\n",
        encoding="utf-8",
    )

    with override_settings(BASE_DIR=tmp_path):
        content = str(rendered_changelog())

    assert "<h2>Build 20260713-1404</h2>" in content
    assert "13.07.2026, 14:04 Uhr" in content
    assert "<li>Dokumente können zusammengeführt werden.</li>" in content
    assert "{{ build_" not in content

    build_version.cache_clear()


def test_canonical_changelog_contains_chronological_build_history():
    content = Path("CHANGELOG.md").read_text(encoding="utf-8")
    builds = [
        "## Build {{ build_number }}",
        "## Build 20260810-1230",
        "## Build 20260806-1453",
        "## Build 20260805-1020",
        "## Build 20260707-0054",
        "## Build 20260706-1714",
    ]

    positions = [content.index(build) for build in builds]

    assert positions == sorted(positions)
    assert "### Neuerungen" in content
    assert "### Änderungen" in content


@override_settings(DOKSIO_BUILD_VERSION="")
def test_changelog_refreshes_when_canonical_file_changes(tmp_path, monkeypatch):
    build_version.cache_clear()
    build_datetime.cache_clear()
    rendered_changelog.cache_clear()
    monkeypatch.setattr("doksio.project.version.BUILD_METADATA_DIR", tmp_path / "opt")
    monkeypatch.setattr("doksio.project.version._git_value", lambda _format: "")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Erste Fassung\n", encoding="utf-8")

    with override_settings(BASE_DIR=tmp_path):
        first_content = str(rendered_changelog())
        changelog.write_text("# Historische Fassung\n", encoding="utf-8")
        refreshed_content = str(rendered_changelog())

    assert "Erste Fassung" in first_content
    assert "Historische Fassung" in refreshed_content

    build_version.cache_clear()
    build_datetime.cache_clear()
    rendered_changelog.cache_clear()
