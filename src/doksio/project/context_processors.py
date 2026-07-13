from __future__ import annotations

from doksio.project.version import build_version


def doksio_version(_request):
    return {
        "doksio_build_version": build_version(),
    }
