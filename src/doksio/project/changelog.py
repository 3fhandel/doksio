from __future__ import annotations

from functools import lru_cache

import markdown
from django.conf import settings
from django.utils import timezone
from django.utils.safestring import SafeString, mark_safe

from doksio.project.version import build_datetime, build_version


@lru_cache
def _rendered_changelog(
    source: str,
    version: str,
    formatted_timestamp: str,
) -> SafeString:
    source = source.replace("{{ build_number }}", version)
    source = source.replace("{{ build_datetime }}", formatted_timestamp)
    return mark_safe(
        markdown.markdown(
            source,
            extensions=["extra", "sane_lists"],
            output_format="html",
        )
    )


def rendered_changelog() -> SafeString:
    changelog_path = settings.BASE_DIR / "CHANGELOG.md"
    try:
        source = changelog_path.read_text(encoding="utf-8")
    except OSError:
        source = (
            "# Doksio Änderungsprotokoll\n\n"
            "Das Änderungsprotokoll ist in diesem Build nicht verfügbar."
        )

    timestamp = build_datetime()
    formatted_timestamp = (
        timezone.localtime(timestamp).strftime("%d.%m.%Y, %H:%M Uhr")
        if timestamp is not None
        else "nicht verfügbar"
    )
    return _rendered_changelog(
        source,
        build_version(),
        formatted_timestamp,
    )


rendered_changelog.cache_clear = _rendered_changelog.cache_clear  # type: ignore[attr-defined]
