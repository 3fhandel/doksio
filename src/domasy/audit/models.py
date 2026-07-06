from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Append-only event record for traceability."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    event_type = models.CharField(max_length=120)
    object_type = models.CharField(max_length=120)
    object_id = models.CharField(max_length=120)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "event_type"]),
            models.Index(fields=["tenant", "object_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.object_type}:{self.object_id}"
