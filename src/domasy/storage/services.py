from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.utils.text import get_valid_filename

from domasy.audit.services import RecordAuditEvent
from domasy.documents.models import Document, DocumentFile
from domasy.tenancy.models import Tenant


def _iter_chunks(file_obj: BinaryIO, chunk_size: int = 1024 * 1024):
    if hasattr(file_obj, "chunks"):
        yield from file_obj.chunks()
        return

    while chunk := file_obj.read(chunk_size):
        yield chunk


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    safe_name = get_valid_filename(name)
    return safe_name or "document.bin"


@dataclass(frozen=True)
class StoreImmutableFile:
    tenant: Tenant
    document: Document
    file_obj: BinaryIO
    original_filename: str
    content_type: str = "application/octet-stream"
    file_kind: str = DocumentFile.Kind.ORIGINAL
    created_by: get_user_model() | None = None
    derivative_of: DocumentFile | None = None

    @transaction.atomic
    def execute(self) -> DocumentFile:
        if self.document.tenant_id != self.tenant.id:
            raise ValueError("Document belongs to a different tenant.")

        if self.derivative_of and self.derivative_of.document_id != self.document.id:
            raise ValueError("Derivative source belongs to a different document.")

        digest = hashlib.sha256()
        byte_size = 0

        with SpooledTemporaryFile(max_size=10 * 1024 * 1024) as buffered_file:
            for chunk in _iter_chunks(self.file_obj):
                digest.update(chunk)
                byte_size += len(chunk)
                buffered_file.write(chunk)

            buffered_file.seek(0)
            sha256 = digest.hexdigest()
            version = self._next_version()
            storage_key = self._build_storage_key()

            if default_storage.exists(storage_key):
                raise FileExistsError(f"Storage key already exists: {storage_key}")

            saved_key = default_storage.save(storage_key, File(buffered_file))

            document_file = DocumentFile.objects.create(
                tenant=self.tenant,
                document=self.document,
                file_kind=self.file_kind,
                version=version,
                storage_key=saved_key,
                original_filename=_safe_filename(self.original_filename),
                content_type=self.content_type,
                byte_size=byte_size,
                sha256=sha256,
                derivative_of=self.derivative_of,
                created_by=self.created_by,
            )

        RecordAuditEvent(
            tenant=self.tenant,
            actor=self.created_by,
            event_type="document_file.stored",
            object_type="documents.DocumentFile",
            object_id=str(document_file.id),
            data={
                "document_id": self.document.id,
                "file_kind": self.file_kind,
                "version": version,
                "sha256": sha256,
                "byte_size": byte_size,
                "storage_key": saved_key,
            },
        ).execute()

        return document_file

    def _next_version(self) -> int:
        latest_version = (
            DocumentFile.objects.select_for_update()
            .filter(document=self.document, file_kind=self.file_kind)
            .aggregate(max_version=Max("version"))["max_version"]
        )
        return (latest_version or 0) + 1

    def _build_storage_key(self) -> str:
        safe_filename = _safe_filename(self.original_filename)
        return (
            f"tenants/{self.tenant.id}/documents/{self.document.id}/"
            f"files/{uuid.uuid4()}/{safe_filename}"
        )
