from __future__ import annotations

from doksio.helpcenter.catalog import contextual_help_topic


def contextual_help(request):
    if not request.user.is_authenticated:
        return {"contextual_help_topic": None}
    tenant_slug = None
    if request.resolver_match is not None:
        tenant_slug = request.resolver_match.kwargs.get("tenant_slug")
    if not tenant_slug:
        return {"contextual_help_topic": None}
    return {
        "contextual_help_topic": contextual_help_topic(request.resolver_match),
    }

