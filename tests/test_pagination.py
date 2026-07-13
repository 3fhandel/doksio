from __future__ import annotations

from django.core.paginator import Paginator

from doksio.documents.templatetags.doksio_extras import elided_page_range


def test_elided_page_range_keeps_large_pagination_compact():
    paginator = Paginator(range(500), 10)
    page = paginator.page(25)

    page_numbers = list(elided_page_range(page))

    assert page_numbers == [1, "…", 24, 25, 26, "…", 50]
