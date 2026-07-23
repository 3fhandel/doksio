"""Application services for document ingestion."""

from __future__ import annotations

import imaplib
import mimetypes
import re
import shlex
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage as ParsedEmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from fnmatch import fnmatch
from io import BytesIO
from typing import BinaryIO

from django.contrib.auth import get_user_model
from django.core.mail import get_connection
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from doksio.accounts.models import Notification, TenantMembership
from doksio.accounts.permissions import TenantPermissions
from doksio.accounts.services import CreateNotification
from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import Document, DocumentSpace
from doksio.documents.services import (
    CreateDocumentFromUpload,
    DuplicateDocumentError,
    SetDocumentTags,
)
from doksio.ingestion.models import (
    EmailAutoReplyRecipient,
    ImportJob,
    ImportSource,
    TenantSmtpSettings,
)
from doksio.project.email import (
    BrandedEmailMultiAlternatives as EmailMultiAlternatives,
    attach_branded_html,
)
from doksio.tenancy.models import Tenant


@dataclass(frozen=True)
class ResolveImportDocumentSpace:
    tenant: Tenant
    source: ImportSource
    original_filename: str

    def execute(self) -> DocumentSpace:
        if self.source.tenant_id != self.tenant.id:
            raise ValueError("Import source belongs to a different tenant.")

        if self.source.target_strategy == ImportSource.TargetStrategy.RULES:
            resolved_space = self._space_from_rules()
            if resolved_space is not None:
                return resolved_space

        return self.source.document_space

    def _space_from_rules(self) -> DocumentSpace | None:
        rules = (self.source.settings or {}).get("routing_rules", [])
        filename = self.original_filename.rsplit("/", 1)[-1]
        for rule in rules:
            pattern = rule.get("pattern", "")
            if pattern and fnmatch(filename, pattern):
                document_space_id = rule.get("document_space_id")
                if not document_space_id:
                    continue
                return DocumentSpace.objects.get(
                    id=document_space_id,
                    tenant=self.tenant,
                    is_active=True,
                )
        return None


@dataclass(frozen=True)
class ResolveManualUploadDocumentSpace:
    tenant: Tenant
    original_filename: str

    def execute(self) -> tuple[DocumentSpace, ImportSource]:
        sources = ImportSource.objects.filter(
            tenant=self.tenant,
            source_type=ImportSource.SourceType.UPLOAD,
            is_active=True,
        )
        strategy_order = {
            ImportSource.TargetStrategy.RULES: 0,
            ImportSource.TargetStrategy.INTELLIGENT: 1,
            ImportSource.TargetStrategy.FIXED: 2,
        }
        for source in sorted(
            sources,
            key=lambda source: (
                strategy_order.get(source.target_strategy, 99),
                source.name.lower(),
                source.id,
            ),
        ):
            document_space = ResolveImportDocumentSpace(
                tenant=self.tenant,
                source=source,
                original_filename=self.original_filename,
            ).execute()
            return document_space, source
        raise ValueError("Keine aktive Upload-Importstrategie gefunden.")


@dataclass(frozen=True)
class ImportDocument:
    tenant: Tenant
    document_space: DocumentSpace
    file_obj: BinaryIO
    original_filename: str
    content_type: str
    source: ImportSource | None = None
    title: str = ""
    actor: get_user_model() | None = None
    metadata: dict | None = None

    def execute(self) -> tuple[Document, ImportJob]:
        if self.document_space.tenant_id != self.tenant.id:
            raise ValueError("Import document space belongs to a different tenant.")
        if self.source and self.source.tenant_id != self.tenant.id:
            raise ValueError("Import source belongs to a different tenant.")
        if (
            self.source
            and self.source.target_strategy == ImportSource.TargetStrategy.FIXED
            and self.source.document_space_id != self.document_space.id
        ):
            raise ValueError("Import source belongs to a different document space.")

        import_job = ImportJob.objects.create(
            tenant=self.tenant,
            source=self.source,
            document_space=self.document_space,
            original_filename=self.original_filename,
            content_type=self.content_type,
            status=ImportJob.Status.PROCESSING,
            metadata=self.metadata or {},
        )
        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.actor,
            event_type="import_job.received",
            object_type="ingestion.ImportJob",
            object_id=str(import_job.id),
            data={
                "source_id": self.source.id if self.source else None,
                "document_space_id": self.document_space.id,
                "original_filename": self.original_filename,
                "content_type": self.content_type,
            },
        ).execute()

        try:
            document, _document_file = CreateDocumentFromUpload(
                tenant=self.tenant,
                title=self.title,
                space=self.document_space,
                file_obj=self.file_obj,
                original_filename=self.original_filename,
                content_type=self.content_type,
                created_by=self.actor,
                auto_start_ocr=(
                    self.source.auto_start_ocr if self.source is not None else None
                ),
                auto_extract_einvoice=True,
                auto_start_workflows=(
                    self.source.start_workflows if self.source is not None else True
                ),
            ).execute()
            if self.source and self.source.default_tags:
                SetDocumentTags(
                    document=document,
                    tag_names=self.source.default_tags,
                    actor=self.actor,
                ).execute()
            import_job.document = document
            import_job.status = ImportJob.Status.IMPORTED
            import_job.message = "Dokument wurde importiert."
            import_job.processed_at = timezone.now()
            import_job.save(
                update_fields=[
                    "document",
                    "status",
                    "message",
                    "processed_at",
                    "updated_at",
                ]
            )
            RecordAuditEvent(
                tenant=self.tenant,
                actor=self.actor,
                event_type="import_job.imported",
                object_type="ingestion.ImportJob",
                object_id=str(import_job.id),
                data={
                    "document_id": document.id,
                    "source_id": self.source.id if self.source else None,
                },
            ).execute()
            return document, import_job
        except Exception as exc:
            import_job.status = ImportJob.Status.FAILED
            import_job.message = str(exc)
            import_job.processed_at = timezone.now()
            import_job.save(
                update_fields=["status", "message", "processed_at", "updated_at"]
            )
            RecordAuditEvent(
                tenant=self.tenant,
                actor=self.actor,
                event_type="import_job.failed",
                object_type="ingestion.ImportJob",
                object_id=str(import_job.id),
                data={
                    "source_id": self.source.id if self.source else None,
                    "error": str(exc),
                },
            ).execute()
            _create_import_failed_notifications(import_job)
            raise


def _create_import_failed_notifications(import_job: ImportJob) -> None:
    link_url = reverse(
        "documents:audit_log",
        kwargs={"tenant_slug": import_job.tenant.slug},
    )
    memberships = TenantMembership.objects.filter(
        tenant=import_job.tenant,
        is_active=True,
        user__is_active=True,
    ).filter(
        Q(role__permissions__code=TenantPermissions.AUDIT_VIEW)
        | Q(roles__permissions__code=TenantPermissions.AUDIT_VIEW)
        | Q(role__permissions__code=TenantPermissions.DOCUMENT_SPACES_MANAGE)
        | Q(roles__permissions__code=TenantPermissions.DOCUMENT_SPACES_MANAGE)
    )
    recipients = get_user_model().objects.filter(
        id__in=memberships.values("user_id"),
        is_active=True,
    ).distinct()
    source_name = import_job.source.name if import_job.source else "Manueller Import"
    for recipient in recipients:
        CreateNotification(
            tenant=import_job.tenant,
            recipient=recipient,
            notification_type=Notification.Type.IMPORT_FAILED,
            title="Importfehler",
            body=(
                f"{import_job.original_filename} aus {source_name} konnte nicht "
                "importiert werden."
            ),
            link_url=link_url,
        ).execute()


@dataclass(frozen=True)
class EmailImportAttachment:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class EmailAttachmentScan:
    matched: list[EmailImportAttachment]
    ignored_filenames: list[str] = field(default_factory=list)


@dataclass
class EmailImportResult:
    checked_messages: int = 0
    matched_attachments: int = 0
    ignored_attachments: int = 0
    imported_documents: int = 0
    duplicate_documents: int = 0
    failed_attachments: int = 0
    unprocessable_messages: int = 0
    errors: list[str] = field(default_factory=list)


def _split_imap_search_criteria(value: str) -> list[str]:
    criteria = (value or "UNSEEN").strip()
    if not criteria:
        return ["UNSEEN"]
    try:
        return shlex.split(criteria)
    except ValueError:
        return criteria.split()


def _email_address_header(message: ParsedEmailMessage, name: str) -> str:
    values = message.get_all(name, [])
    return ", ".join(str(value) for value in values if value)


def _attachment_filename(
    *,
    filename: str | None,
    content_type: str,
    index: int,
) -> str:
    if filename:
        return filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    extension = mimetypes.guess_extension(content_type) or ".bin"
    return f"mail-anhang-{index}{extension}"


def _attachment_content_type(filename: str, content_type: str) -> str:
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if (
        normalized_content_type
        and normalized_content_type != "application/octet-stream"
    ):
        return normalized_content_type
    guessed_content_type, _encoding = mimetypes.guess_type(filename)
    return guessed_content_type or "application/octet-stream"


def _matching_email_attachments(
    message: ParsedEmailMessage,
    pattern: str,
) -> EmailAttachmentScan:
    attachments = []
    ignored_filenames = []
    normalized_pattern = pattern or "*"
    for index, part in enumerate(message.walk(), start=1):
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        raw_filename = part.get_filename()
        if disposition != "attachment" and not raw_filename:
            continue
        filename = _attachment_filename(
            filename=raw_filename,
            content_type=part.get_content_type(),
            index=index,
        )
        if not fnmatch(filename.casefold(), normalized_pattern.casefold()):
            ignored_filenames.append(filename)
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            content = part.get_content()
            payload = content.encode() if isinstance(content, str) else bytes(content)
        attachments.append(
            EmailImportAttachment(
                filename=filename,
                content_type=_attachment_content_type(
                    filename,
                    part.get_content_type(),
                ),
                content=payload,
            )
        )
    return EmailAttachmentScan(
        matched=attachments,
        ignored_filenames=ignored_filenames,
    )


def _smtp_from_email(smtp_settings: TenantSmtpSettings) -> str:
    from_email = smtp_settings.from_email or smtp_settings.username
    if smtp_settings.from_name and from_email:
        return f"{smtp_settings.from_name} <{from_email}>"
    return from_email


def _smtp_connection(smtp_settings: TenantSmtpSettings):
    return get_connection(
        host=smtp_settings.host,
        port=smtp_settings.port,
        username=smtp_settings.username or None,
        password=smtp_settings.password or None,
        use_tls=smtp_settings.security == TenantSmtpSettings.Security.STARTTLS,
        use_ssl=smtp_settings.security == TenantSmtpSettings.Security.SSL,
    )


def _email_reply_recipient(original_message: ParsedEmailMessage) -> str:
    header_values = original_message.get_all("Reply-To", [])
    if not header_values:
        header_values = original_message.get_all("From", [])
    for _display_name, address in getaddresses(
        [str(value) for value in header_values]
    ):
        normalized_address = address.strip().casefold()
        if normalized_address:
            return normalized_address
    return ""


def _send_email_import_reply(
    *,
    source: ImportSource,
    original_message: ParsedEmailMessage,
    subject: str,
    body: str,
    reply_type: str,
    once_per_sender: bool = False,
) -> bool:
    recipient = _email_reply_recipient(original_message)
    smtp_settings = TenantSmtpSettings.objects.filter(
        tenant=source.tenant,
        is_active=True,
    ).first()
    if not recipient or smtp_settings is None or not body:
        return False

    recipient_record = None
    if once_per_sender:
        recipient_record, created = EmailAutoReplyRecipient.objects.get_or_create(
            tenant=source.tenant,
            source=source,
            recipient=recipient,
            reply_type=reply_type,
            defaults={"subject": (subject or "Doksio Import")[:255]},
        )
        if not created:
            RecordAuditEvent(
                tenant=source.tenant,
                actor=None,
                event_type="email_import_reply.suppressed",
                object_type="ingestion.ImportSource",
                object_id=str(source.id),
                data={
                    "recipient": recipient,
                    "reply_type": reply_type,
                    "reason": "once_per_sender",
                },
            ).execute()
            return False

    email = EmailMultiAlternatives(
        subject=subject or "Doksio Import",
        body=body,
        from_email=_smtp_from_email(smtp_settings),
        to=[recipient],
        connection=_smtp_connection(smtp_settings),
    )
    attach_branded_html(
        email,
        heading=subject or "Doksio Import",
        content=body,
        tenant_name=source.tenant.name,
    )
    try:
        sent_count = email.send()
    except Exception:
        if recipient_record is not None:
            recipient_record.delete()
        raise
    if not sent_count:
        if recipient_record is not None:
            recipient_record.delete()
        return False

    if recipient_record is not None:
        recipient_record.sent_at = timezone.now()
        recipient_record.save(update_fields=["sent_at"])
    RecordAuditEvent(
        tenant=source.tenant,
        actor=None,
        event_type="email_import_reply.sent",
        object_type="ingestion.ImportSource",
        object_id=str(source.id),
        data={
            "recipient": recipient,
            "reply_type": reply_type,
            "once_per_sender": once_per_sender,
        },
    ).execute()
    return True


def _connect_imap(settings: dict):
    host = settings.get("host", "")
    port = int(settings.get("port") or 993)
    security = settings.get("security", "ssl")
    if security == "ssl":
        connection = imaplib.IMAP4_SSL(host, port)
    else:
        connection = imaplib.IMAP4(host, port)
        if security == "starttls":
            connection.starttls()
    connection.login(settings.get("username", ""), settings.get("password", ""))
    return connection


def _imap_ok(status: str | bytes) -> bool:
    return status == "OK" or status == b"OK"


def _raw_message_from_fetch_data(data) -> bytes | None:
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _email_received_at(
    fetch_data,
    message: ParsedEmailMessage,
) -> datetime | None:
    for item in fetch_data or []:
        response_metadata = item[0] if isinstance(item, tuple) and item else item
        if not isinstance(response_metadata, bytes):
            continue
        match = re.search(
            rb'INTERNALDATE\s+"([^"]+)"',
            response_metadata,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        with suppress(TypeError, ValueError, OverflowError):
            received_at = parsedate_to_datetime(
                match.group(1).decode("ascii", errors="replace")
            )
            if timezone.is_naive(received_at):
                received_at = timezone.make_aware(received_at)
            return received_at

    for received_header in message.get_all("Received", []):
        _separator, delimiter, timestamp = str(received_header).rpartition(";")
        if not delimiter:
            continue
        with suppress(TypeError, ValueError, OverflowError):
            received_at = parsedate_to_datetime(timestamp.strip())
            if timezone.is_naive(received_at):
                received_at = timezone.make_aware(received_at)
            return received_at
    return None


@dataclass(frozen=True)
class ProcessEmailImportSource:
    source: ImportSource
    imap_factory: object | None = None

    def execute(self) -> EmailImportResult:
        if self.source.source_type != ImportSource.SourceType.EMAIL:
            raise ValueError("Importquelle ist keine E-Mail-Quelle.")
        if not self.source.is_active:
            return EmailImportResult()

        email_settings = (self.source.settings or {}).get("email", {})
        connection = self._connect(email_settings)
        result = EmailImportResult()
        try:
            self._select_mailbox(connection, email_settings)
            message_ids = self._search_messages(connection, email_settings)
            for message_id in message_ids:
                result.checked_messages += 1
                self._process_message(
                    connection=connection,
                    message_id=message_id,
                    email_settings=email_settings,
                    result=result,
                )
        finally:
            self._logout(connection)

        self._write_poll_state(result)
        return result

    def _connect(self, email_settings: dict):
        if self.imap_factory is not None:
            return self.imap_factory(email_settings)
        return _connect_imap(email_settings)

    def _select_mailbox(self, connection, email_settings: dict) -> None:
        status, _data = connection.select(email_settings.get("mailbox") or "INBOX")
        if not _imap_ok(status):
            raise ValueError("IMAP-Mailbox konnte nicht geöffnet werden.")

    def _search_messages(self, connection, email_settings: dict) -> list[bytes]:
        criteria = _split_imap_search_criteria(
            email_settings.get("search_criteria", "UNSEEN")
        )
        status, data = connection.search(None, *criteria)
        if not _imap_ok(status):
            raise ValueError("IMAP-Suche ist fehlgeschlagen.")
        if not data:
            return []
        return data[0].split()

    def _process_message(
        self,
        *,
        connection,
        message_id: bytes,
        email_settings: dict,
        result: EmailImportResult,
    ) -> None:
        status, data = connection.fetch(message_id, "(RFC822 INTERNALDATE)")
        if not _imap_ok(status):
            result.errors.append(f"Mail {message_id!r} konnte nicht gelesen werden.")
            return
        raw_message = _raw_message_from_fetch_data(data)
        if raw_message is None:
            result.errors.append(f"Mail {message_id!r} enthält keine Rohdaten.")
            return

        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        received_at = _email_received_at(data, message)
        attachment_scan = _matching_email_attachments(
            message,
            email_settings.get("attachment_pattern", "*"),
        )
        attachments = attachment_scan.matched
        result.ignored_attachments += len(attachment_scan.ignored_filenames)
        if not attachments:
            result.unprocessable_messages += 1
            if attachment_scan.ignored_filenames:
                result.errors.append(
                    "Mail "
                    f"{message_id!r}: Keine Anhänge passend zum Muster "
                    f"{email_settings.get('attachment_pattern', '*')}; "
                    "ignoriert: "
                    f"{', '.join(attachment_scan.ignored_filenames)}"
                )
            self._handle_unprocessable_message(
                connection=connection,
                message_id=message_id,
                email_settings=email_settings,
                message=message,
            )
            return

        before_failures = result.failed_attachments
        before_processed = (
            result.imported_documents
            + result.duplicate_documents
            + result.failed_attachments
        )
        result.matched_attachments += len(attachments)
        for attachment in attachments:
            self._import_attachment(
                attachment=attachment,
                message=message,
                received_at=received_at,
                result=result,
            )

        after_processed = (
            result.imported_documents
            + result.duplicate_documents
            + result.failed_attachments
        )
        if after_processed == before_processed:
            result.failed_attachments += len(attachments)
            result.errors.append(
                f"Mail {message_id!r}: "
                "Passende Anhänge gefunden, aber kein Anhang wurde verarbeitet."
            )
            return

        if result.failed_attachments == before_failures:
            self._finalize_processed_message(
                connection=connection,
                message_id=message_id,
                email_settings=email_settings,
            )
            self._send_success_reply(email_settings, message)

    def _import_attachment(
        self,
        *,
        attachment: EmailImportAttachment,
        message: ParsedEmailMessage,
        received_at,
        result: EmailImportResult,
    ) -> None:
        try:
            document_space = ResolveImportDocumentSpace(
                tenant=self.source.tenant,
                source=self.source,
                original_filename=attachment.filename,
            ).execute()
            document, import_job = ImportDocument(
                tenant=self.source.tenant,
                source=self.source,
                document_space=document_space,
                file_obj=BytesIO(attachment.content),
                original_filename=attachment.filename,
                content_type=attachment.content_type,
                metadata={
                    "method": "email",
                    "message_id": str(message.get("Message-ID", "")),
                    "subject": str(message.get("Subject", "")),
                    "from": _email_address_header(message, "From"),
                    "received_at": (
                        received_at.isoformat() if received_at is not None else ""
                    ),
                },
            ).execute()
            RecordAuditEvent(
                tenant=self.source.tenant,
                actor=None,
                event_type="document.email_received",
                object_type="documents.Document",
                object_id=str(document.id),
                data={
                    "document_id": document.id,
                    "import_job_id": import_job.id,
                    "source_id": self.source.id,
                    "sender": _email_address_header(message, "From"),
                    "received_at": (
                        received_at.isoformat() if received_at is not None else ""
                    ),
                    "subject": str(message.get("Subject", "")),
                    "message_id": str(message.get("Message-ID", "")),
                },
            ).execute()
            result.imported_documents += 1
        except DuplicateDocumentError:
            result.duplicate_documents += 1
        except Exception as exc:
            result.failed_attachments += 1
            result.errors.append(f"{attachment.filename}: {exc}")

    def _finalize_processed_message(
        self,
        *,
        connection,
        message_id: bytes,
        email_settings: dict,
    ) -> None:
        move_to = (email_settings.get("move_processed_to") or "").strip()
        if move_to:
            self._copy_to_mailbox(connection, message_id, move_to)
            self._delete_message(connection, message_id)
            return
        if email_settings.get("delete_after_import"):
            self._delete_message(connection, message_id)
            return
        if email_settings.get("mark_seen", True):
            connection.store(message_id, "+FLAGS", "\\Seen")

    def _handle_unprocessable_message(
        self,
        *,
        connection,
        message_id: bytes,
        email_settings: dict,
        message: ParsedEmailMessage,
    ) -> None:
        action = email_settings.get("unprocessable_action", "keep")
        if action == "mark_seen":
            connection.store(message_id, "+FLAGS", "\\Seen")
        elif action == "delete":
            self._delete_message(connection, message_id)
        elif action == "move":
            move_to = email_settings.get("unprocessable_move_to", "").strip()
            if move_to:
                self._copy_to_mailbox(connection, message_id, move_to)
                self._delete_message(connection, message_id)
        if email_settings.get("unprocessable_reply_enabled"):
            _send_email_import_reply(
                source=self.source,
                original_message=message,
                subject=email_settings.get("unprocessable_reply_subject", ""),
                body=email_settings.get("unprocessable_reply_body", ""),
                reply_type=EmailAutoReplyRecipient.ReplyType.UNPROCESSABLE,
                once_per_sender=email_settings.get(
                    "unprocessable_reply_once_per_sender",
                    False,
                ),
            )

    def _send_success_reply(
        self,
        email_settings: dict,
        message: ParsedEmailMessage,
    ) -> None:
        if not email_settings.get("success_reply_enabled"):
            return
        _send_email_import_reply(
            source=self.source,
            original_message=message,
            subject=email_settings.get("success_reply_subject", ""),
            body=email_settings.get("success_reply_body", ""),
            reply_type=EmailAutoReplyRecipient.ReplyType.SUCCESS,
            once_per_sender=email_settings.get(
                "success_reply_once_per_sender",
                False,
            ),
        )

    def _copy_to_mailbox(self, connection, message_id: bytes, mailbox: str) -> None:
        status, _data = connection.copy(message_id, mailbox)
        if not _imap_ok(status):
            connection.create(mailbox)
            status, _data = connection.copy(message_id, mailbox)
        if not _imap_ok(status):
            raise ValueError(f"Mail konnte nicht nach {mailbox} verschoben werden.")

    def _delete_message(self, connection, message_id: bytes) -> None:
        connection.store(message_id, "+FLAGS", "\\Deleted")
        connection.expunge()

    def _logout(self, connection) -> None:
        with suppress(Exception):
            connection.close()
        with suppress(Exception):
            connection.logout()

    def _write_poll_state(self, result: EmailImportResult) -> None:
        settings = self.source.settings or {}
        email_settings = settings.get("email", {})
        email_settings["last_checked_at"] = timezone.now().isoformat()
        email_settings["last_result"] = {
            "checked_messages": result.checked_messages,
            "matched_attachments": result.matched_attachments,
            "ignored_attachments": result.ignored_attachments,
            "imported_documents": result.imported_documents,
            "duplicate_documents": result.duplicate_documents,
            "failed_attachments": result.failed_attachments,
            "unprocessable_messages": result.unprocessable_messages,
            "errors": result.errors[-10:],
        }
        settings["email"] = email_settings
        self.source.settings = settings
        self.source.save(update_fields=["settings", "updated_at"])
        RecordAuditEvent(
            tenant=self.source.tenant,
            actor=None,
            event_type="email_import_source.polled",
            object_type="ingestion.ImportSource",
            object_id=str(self.source.id),
            data=email_settings["last_result"],
        ).execute()


@dataclass(frozen=True)
class ProcessDueEmailImportSources:
    imap_factory: object | None = None
    force: bool = False

    def execute(self) -> EmailImportResult:
        total = EmailImportResult()
        for source in ImportSource.objects.select_related(
            "tenant",
            "document_space",
        ).filter(
            source_type=ImportSource.SourceType.EMAIL,
            is_active=True,
        ):
            if not self.force and not self._is_due(source):
                continue
            try:
                result = ProcessEmailImportSource(
                    source=source,
                    imap_factory=self.imap_factory,
                ).execute()
            except Exception as exc:
                total.errors.append(f"{source.name}: {exc}")
                continue
            total.checked_messages += result.checked_messages
            total.matched_attachments += result.matched_attachments
            total.ignored_attachments += result.ignored_attachments
            total.imported_documents += result.imported_documents
            total.duplicate_documents += result.duplicate_documents
            total.failed_attachments += result.failed_attachments
            total.unprocessable_messages += result.unprocessable_messages
            total.errors.extend(result.errors)
        return total

    def _is_due(self, source: ImportSource) -> bool:
        email_settings = (source.settings or {}).get("email", {})
        last_checked_at = email_settings.get("last_checked_at")
        if not last_checked_at:
            return True
        try:
            last_checked = timezone.datetime.fromisoformat(last_checked_at)
        except ValueError:
            return True
        if timezone.is_naive(last_checked):
            last_checked = timezone.make_aware(last_checked)
        interval_seconds = int(email_settings.get("poll_interval_seconds") or 300)
        return (timezone.now() - last_checked).total_seconds() >= interval_seconds
