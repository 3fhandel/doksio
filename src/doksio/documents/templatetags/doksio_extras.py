from __future__ import annotations

from time import perf_counter

from django import template
from django.core.exceptions import ObjectDoesNotExist

from doksio.documents.mentions import render_mentions
from doksio.documents.models import DocumentFile

register = template.Library()

CONTENT_TYPE_LABELS = {
    "application/pdf": "PDF",
    "application/msword": "DOC",
    "application/vnd.oasis.opendocument.text": "ODT",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
    "image/tiff": "TIFF",
    "text/plain": "Text",
}

BROWSER_IMAGE_PREVIEW_CONTENT_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


@register.simple_tag
def page_rendering_time(request) -> str:
    started_at = getattr(request, "_doksio_request_started_at", None)
    if started_at is None:
        return "0.000 s"
    return f"{perf_counter() - started_at:.3f} s"


@register.filter
def get_item(mapping, key):
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return mapping[key]


@register.filter
def widget_attr(bound_field, key):
    return bound_field.field.widget.attrs.get(key)


@register.filter
def percent_of(value, total):
    try:
        value = int(value or 0)
        total = int(total or 0)
    except (TypeError, ValueError):
        return 0
    if total <= 0:
        return 0
    return min(100, max(0, round(value / total * 100)))


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
def document_original_file(document):
    original = None
    for file in document.files.all():
        if file.file_kind == DocumentFile.Kind.ORIGINAL:
            original = file
    return original


@register.filter
def document_quick_preview_file(document):
    original_image = None
    converted_pdf = None
    image_preview = None
    for file in document.files.all():
        content_type = file.content_type.split(";", 1)[0].strip().lower()
        if (
            file.file_kind == DocumentFile.Kind.ORIGINAL
            and content_type == "application/pdf"
        ):
            return file
        if (
            file.file_kind == DocumentFile.Kind.ORIGINAL
            and content_type.startswith("image/")
        ):
            original_image = file
        elif (
            file.file_kind == DocumentFile.Kind.DERIVATIVE
            and content_type == "application/pdf"
        ):
            converted_pdf = file
        elif (
            file.file_kind == DocumentFile.Kind.PREVIEW
            and content_type.startswith("image/")
        ):
            image_preview = file
    return original_image or converted_pdf or image_preview


@register.filter
def import_batch_preview_kind(item):
    content_type = item.content_type.split(";", 1)[0].strip().lower()
    if content_type == "application/pdf":
        return "pdf"
    if content_type in BROWSER_IMAGE_PREVIEW_CONTENT_TYPES:
        return "image"
    return ""


@register.filter
def document_has_fulltext(document):
    try:
        search_index = document.search_index
    except ObjectDoesNotExist:
        return False
    return bool(search_index.ocr_text.strip())


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
