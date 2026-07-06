from __future__ import annotations

from django.core.paginator import Page, Paginator
from django.http import HttpRequest


def paginate_queryset(
    request: HttpRequest,
    queryset,
    *,
    page_param: str = "page",
    per_page: int = 25,
) -> Page:
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get(page_param))
