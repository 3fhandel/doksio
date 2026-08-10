from __future__ import annotations

from doksio.project.changelog import rendered_changelog
from doksio.project.version import build_datetime, build_version


def doksio_version(_request):
    return {
        "doksio_build_version": build_version(),
        "doksio_build_datetime": build_datetime(),
        "doksio_changelog": rendered_changelog(),
    }
