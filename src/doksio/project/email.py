from __future__ import annotations

from email.mime.image import MIMEImage

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail.message import SafeMIMEMultipart
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string


class BrandedEmailMultiAlternatives(EmailMultiAlternatives):
    """Email with inline resources nested correctly inside its HTML alternative."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.inline_attachments = []

    def attach_inline_image(
        self,
        *,
        content: bytes,
        content_id: str,
        filename: str,
    ) -> None:
        image = MIMEImage(content, _subtype="png")
        image.add_header("Content-ID", f"<{content_id}>")
        image.add_header("Content-Disposition", "inline", filename=filename)
        self.inline_attachments.append(image)

    def _create_alternatives(self, msg):
        if not self.alternatives:
            return msg

        encoding = self.encoding or settings.DEFAULT_CHARSET
        alternatives = SafeMIMEMultipart(
            _subtype=self.alternative_subtype,
            encoding=encoding,
        )
        if self.body:
            alternatives.attach(msg)
        for alternative in self.alternatives:
            alternative_message = self._create_mime_attachment(
                alternative.content,
                alternative.mimetype,
            )
            if alternative.mimetype == "text/html" and self.inline_attachments:
                related = SafeMIMEMultipart(_subtype="related", encoding=encoding)
                related.attach(alternative_message)
                for inline_attachment in self.inline_attachments:
                    related.attach(inline_attachment)
                alternatives.attach(related)
            else:
                alternatives.attach(alternative_message)
        return alternatives


def _logo_content() -> bytes | None:
    logo_path = finders.find("img/doksio-logo-compact.png")
    if not logo_path:
        return None
    with open(logo_path, "rb") as logo_file:
        return logo_file.read()


def attach_branded_html(
    message: BrandedEmailMultiAlternatives,
    *,
    heading: str,
    content: str,
    tenant_name: str = "",
    action_url: str = "",
    action_label: str = "",
    preheader: str = "",
) -> EmailMultiAlternatives:
    """Attach the shared Doksio HTML presentation to an email."""

    html = render_to_string(
        "emails/branded.html",
        {
            "heading": heading,
            "content": content,
            "tenant_name": tenant_name,
            "action_url": action_url,
            "action_label": action_label,
            "preheader": preheader or heading,
        },
    )
    message.attach_alternative(html, "text/html")
    logo_content = _logo_content()
    if logo_content:
        message.attach_inline_image(
            content=logo_content,
            content_id="doksio-logo",
            filename="doksio-logo.png",
        )
    return message
