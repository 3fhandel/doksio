from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth import get_user_model

from domasy.audit.models import AuditEvent
from domasy.tenancy.models import Tenant


@dataclass(frozen=True)
class RecordAuditEvent:
    tenant: Tenant
    event_type: str
    object_type: str
    object_id: str
    actor: get_user_model() | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def execute(self) -> AuditEvent:
        return AuditEvent.objects.create(
            tenant=self.tenant,
            actor=self.actor,
            event_type=self.event_type,
            object_type=self.object_type,
            object_id=self.object_id,
            data=self.data,
        )
