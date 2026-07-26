from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.urls import reverse

from doksio.accounts.models import Notification
from doksio.accounts.services import CreateNotification
from doksio.alarms.models import DocumentAlarm, DocumentAlarmMatch
from doksio.audit.services import RecordAuditEvent
from doksio.documents.models import Document
from doksio.search.services import SearchDocuments


@dataclass(frozen=True)
class EvaluateDocumentAlarms:
    document: Document

    def execute(self) -> int:
        alarms = (
            DocumentAlarm.objects.filter(
                tenant=self.document.tenant,
                is_active=True,
            )
            .select_related("owner", "document_space")
            .prefetch_related("tags")
        )
        created_count = 0
        for alarm in alarms:
            filters = {
                "q": alarm.search_term,
                "box": alarm.document_space,
                "include_child_boxes": alarm.include_child_spaces,
                "tags": list(alarm.tags.all()),
                "document_status": "active",
            }
            matches = SearchDocuments(
                tenant=self.document.tenant,
                filters=filters,
                user=alarm.owner,
            ).execute().filter(id=self.document.id).exists()
            if not matches:
                continue
            with transaction.atomic():
                _match, created = DocumentAlarmMatch.objects.get_or_create(
                    alarm=alarm,
                    document=self.document,
                )
                if not created:
                    continue
                CreateNotification(
                    tenant=self.document.tenant,
                    recipient=alarm.owner,
                    notification_type=Notification.Type.DOCUMENT_ALARM,
                    title=f"Alarm „{alarm.name}“",
                    body=f"Das Dokument „{self.document.title}“ erfüllt den Alarm.",
                    link_url=reverse(
                        "documents:detail",
                        kwargs={
                            "tenant_slug": self.document.tenant.slug,
                            "document_id": self.document.id,
                        },
                    ),
                    document=self.document,
                    channel_override={
                        "in_app": alarm.notify_in_app,
                        "email": alarm.notify_email,
                    },
                ).execute()
                RecordAuditEvent(
                    tenant=self.document.tenant,
                    actor=None,
                    event_type="document_alarm.matched",
                    object_type="alarms.DocumentAlarm",
                    object_id=str(alarm.id),
                    data={
                        "alarm_name": alarm.name,
                        "document_id": self.document.id,
                        "recipient_id": alarm.owner_id,
                        "notify_in_app": alarm.notify_in_app,
                        "notify_email": alarm.notify_email,
                    },
                ).execute()
                created_count += 1
        return created_count
