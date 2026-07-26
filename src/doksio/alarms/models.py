from __future__ import annotations

from django.conf import settings
from django.db import models


class DocumentAlarm(models.Model):
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="document_alarms",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="document_alarms",
    )
    name = models.CharField(max_length=120)
    search_term = models.CharField(max_length=255, blank=True)
    document_space = models.ForeignKey(
        "documents.DocumentSpace",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="document_alarms",
    )
    include_child_spaces = models.BooleanField(default=True)
    tags = models.ManyToManyField(
        "documents.DocumentTag",
        blank=True,
        related_name="document_alarms",
    )
    notify_in_app = models.BooleanField(default=True)
    notify_email = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "owner", "name"],
                name="alarms_unique_owner_name",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "owner", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.name


class DocumentAlarmMatch(models.Model):
    alarm = models.ForeignKey(
        DocumentAlarm,
        on_delete=models.CASCADE,
        related_name="matches",
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="alarm_matches",
    )
    matched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-matched_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["alarm", "document"],
                name="alarms_unique_alarm_document",
            )
        ]
        indexes = [
            models.Index(fields=["alarm", "matched_at"]),
        ]
