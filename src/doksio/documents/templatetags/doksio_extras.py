from __future__ import annotations

from django import template
from django.core.exceptions import ObjectDoesNotExist

from doksio.documents.mentions import render_mentions
from doksio.documents.models import DocumentFile

register = template.Library()

CONTENT_TYPE_LABELS = {
    "application/pdf": "PDF",
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
    "image/tiff": "TIFF",
    "text/plain": "Text",
}


@register.filter
def get_item(mapping, key):
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return mapping[key]


@register.filter
def widget_attr(bound_field, key):
    return bound_field.field.widget.attrs.get(key)


@register.filter
def document_file_type(document):
    files = [
        file
        for file in document.files.all()
        if file.file_kind == DocumentFile.Kind.ORIGINAL
    ]
    file = files[0] if files else None
    if file is None:
        return "-"
    content_type = file.content_type.split(";", 1)[0].strip().lower()
    if content_type in CONTENT_TYPE_LABELS:
        return CONTENT_TYPE_LABELS[content_type]
    if "/" in content_type:
        main_type, subtype = content_type.split("/", 1)
        if main_type == "image":
            return subtype.upper()
        return subtype.upper()
    return content_type or "-"


@register.filter
def document_thumbnail_file(document):
    for file in document.files.all():
        if file.file_kind == DocumentFile.Kind.THUMBNAIL:
            return file
    return None


@register.filter
def display_user(user):
    if user is None:
        return "System"

    try:
        display_name = user.doksio_profile.display_name
    except ObjectDoesNotExist:
        display_name = ""

    display_name = display_name.strip()
    if display_name:
        return display_name

    full_name = user.get_full_name().strip()
    if full_name:
        return full_name

    return user.get_username()


@register.filter
def comment_body_with_mentions(comment):
    return render_mentions(comment.body, comment.mentioned_users.all())


@register.simple_tag(takes_context=True)
def page_url(context, page_param, page_number):
    request = context.get("request")
    if request is None:
        return f"?{page_param}={page_number}"
    params = request.GET.copy()
    params[page_param] = page_number
    return f"?{params.urlencode()}"


@register.simple_tag
def elided_page_range(page_obj):
    return page_obj.paginator.get_elided_page_range(
        page_obj.number,
        on_each_side=1,
        on_ends=1,
    )
