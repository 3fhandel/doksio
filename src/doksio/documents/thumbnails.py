from __future__ import annotations

from io import BytesIO

from django.core.files.storage import default_storage

from doksio.documents.models import DocumentFile
from doksio.storage.services import StoreImmutableFile

PDF_CONTENT_TYPES = {"application/pdf"}
IMAGE_CONTENT_TYPES = {
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}

THUMBNAIL_SIZE = (220, 300)


def supports_thumbnail_content_type(content_type: str) -> bool:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized in PDF_CONTENT_TYPES or normalized in IMAGE_CONTENT_TYPES


def create_thumbnail_for_document_file(
    document_file: DocumentFile,
    *,
    actor=None,
) -> DocumentFile | None:
    if document_file.file_kind != DocumentFile.Kind.ORIGINAL:
        return None

    if document_file.derivatives.filter(file_kind=DocumentFile.Kind.THUMBNAIL).exists():
        return None

    try:
        image_bytes = _render_thumbnail_bytes(document_file)
    except Exception:
        return None

    if image_bytes is None:
        return None

    return StoreImmutableFile(
        tenant=document_file.tenant,
        document=document_file.document,
        file_obj=BytesIO(image_bytes),
        original_filename=_thumbnail_filename(document_file.original_filename),
        content_type="image/jpeg",
        file_kind=DocumentFile.Kind.THUMBNAIL,
        derivative_of=document_file,
        created_by=actor,
    ).execute()


def _render_thumbnail_bytes(document_file: DocumentFile) -> bytes | None:
    normalized = document_file.content_type.split(";", 1)[0].strip().lower()
    if normalized in IMAGE_CONTENT_TYPES:
        return _render_image_thumbnail(document_file)
    if normalized in PDF_CONTENT_TYPES:
        return _render_pdf_thumbnail(document_file)
    return None


def _render_image_thumbnail(document_file: DocumentFile) -> bytes | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    with (
        default_storage.open(document_file.storage_key, "rb") as stored_file,
        Image.open(stored_file) as image,
    ):
        image = ImageOps.exif_transpose(image)
        image.thumbnail(THUMBNAIL_SIZE)
        if image.mode not in ("RGB", "L"):
            background = Image.new("RGB", image.size, "white")
            if image.mode == "RGBA":
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image)
            image = background
        elif image.mode == "L":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
        return output.getvalue()


def _render_pdf_thumbnail(document_file: DocumentFile) -> bytes | None:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None

    with default_storage.open(document_file.storage_key, "rb") as stored_file:
        pdf_bytes = stored_file.read()

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        if len(pdf) == 0:
            return None
        page = pdf[0]
        try:
            bitmap = page.render(scale=1.4)
            image = bitmap.to_pil()
        finally:
            page.close()
    finally:
        pdf.close()

    image.thumbnail(THUMBNAIL_SIZE)
    if image.mode != "RGB":
        image = image.convert("RGB")
    output = BytesIO()
    image.save(output, format="JPEG", quality=82, optimize=True)
    return output.getvalue()


def _thumbnail_filename(original_filename: str) -> str:
    stem = original_filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    return f"{stem or 'thumbnail'}-thumbnail.jpg"
