from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import SendWeeklySummary

logger = logging.getLogger(__name__)


@shared_task
def dispatch_weekly_summaries() -> dict[str, int]:
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    memberships = (
        TenantMembership.objects.filter(
            is_active=True,
            tenant__is_active=True,
            user__is_active=True,
            user__email__gt="",
            user__doksio_profile__weekly_summary_enabled=True,
        )
        .select_related("tenant", "user", "user__doksio_profile")
        .order_by("tenant_id", "user_id")
    )
    result = {"sent": 0, "skipped": 0, "failed": 0}
    for membership in memberships.iterator():
        try:
            delivery = SendWeeklySummary(
                tenant=membership.tenant,
                recipient=membership.user,
                week_start=week_start,
            ).execute()
        except Exception:
            logger.exception(
                "Weekly summary delivery failed for tenant=%s user=%s",
                membership.tenant_id,
                membership.user_id,
            )
            result["failed"] += 1
        else:
            result["sent" if delivery is not None else "skipped"] += 1
    return result
