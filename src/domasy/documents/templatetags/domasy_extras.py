from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.simple_tag(takes_context=True)
def page_url(context, page_param, page_number):
    request = context.get("request")
    if request is None:
        return f"?{page_param}={page_number}"
    params = request.GET.copy()
    params[page_param] = page_number
    return f"?{params.urlencode()}"
