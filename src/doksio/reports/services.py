from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from doksio.accounts.models import TenantMembership
from doksio.documents.models import Document
from doksio.tenancy.models import Tenant
from doksio.workflows.models import WorkflowInstance, WorkflowTask


def _day_start(value: date) -> datetime:
    return timezone.make_aware(datetime.combine(value, time.min))


def _day_end(value: date) -> datetime:
    return timezone.make_aware(datetime.combine(value, time.max))


def _pct(value: int, maximum: int) -> int:
    if maximum <= 0:
        return 0
    return max(4, round((value / maximum) * 100))


def _format_duration(seconds: float | None) -> str:
    if not seconds:
        return "-"
    hours = seconds / 3600
    if hours < 1:
        return f"{round(seconds / 60)} min"
    if hours < 48:
        return f"{hours:.1f} h".replace(".", ",")
    return f"{hours / 24:.1f} Tage".replace(".", ",")


@dataclass(frozen=True)
class BuildTenantReports:
    tenant: Tenant
    days: int = 30

    def execute(self) -> dict:
        today = timezone.localdate()
        start_date = today - timedelta(days=self.days - 1)
        start_at = _day_start(start_date)
        end_at = _day_end(today)

        documents = Document.objects.filter(tenant=self.tenant)
        active_documents = documents.filter(status=Document.Status.ACTIVE)
        period_documents = active_documents.filter(
            created_at__gte=start_at,
            created_at__lte=end_at,
        )
        workflow_instances = WorkflowInstance.objects.filter(tenant=self.tenant)
        workflow_tasks = WorkflowTask.objects.filter(tenant=self.tenant)
        period_tasks = workflow_tasks.filter(
            created_at__gte=start_at,
            created_at__lte=end_at,
        )
        completed_period_tasks = workflow_tasks.filter(
            status=WorkflowTask.Status.COMPLETED,
            completed_at__gte=start_at,
            completed_at__lte=end_at,
        ).select_related("completed_by")

        return {
            "period_label": f"{start_date:%d.%m.%Y} bis {today:%d.%m.%Y}",
            "summary": self._summary(
                active_documents=active_documents,
                period_documents=period_documents,
                workflow_instances=workflow_instances,
                workflow_tasks=workflow_tasks,
                completed_period_tasks=completed_period_tasks,
                start_at=start_at,
            ),
            "document_intake": self._document_intake(period_documents, start_date, today),
            "box_distribution": self._box_distribution(period_documents),
            "workflow_status": self._workflow_status(workflow_instances),
            "open_task_trend": self._open_task_trend(workflow_tasks, start_date, today),
            "workflow_throughput": self._workflow_throughput(period_tasks, start_date, today),
            "user_performance": self._user_performance(completed_period_tasks),
        }

    def _summary(
        self,
        *,
        active_documents,
        period_documents,
        workflow_instances,
        workflow_tasks,
        completed_period_tasks,
        start_at,
    ) -> dict:
        completed_count = completed_period_tasks.count()
        completed_durations = [
            (task.completed_at - task.created_at).total_seconds()
            for task in completed_period_tasks
            if task.completed_at
        ]
        average_seconds = (
            sum(completed_durations) / len(completed_durations)
            if completed_durations
            else None
        )
        new_documents_count = period_documents.count()
        previous_documents_count = active_documents.filter(
            created_at__gte=start_at - timedelta(days=self.days),
            created_at__lt=start_at,
        ).count()
        document_delta = new_documents_count - previous_documents_count

        return {
            "new_documents": new_documents_count,
            "document_delta": document_delta,
            "total_documents": active_documents.count(),
            "running_workflows": workflow_instances.filter(
                status=WorkflowInstance.Status.RUNNING,
            ).count(),
            "completed_workflows": workflow_instances.filter(
                status=WorkflowInstance.Status.COMPLETED,
            ).count(),
            "open_tasks": workflow_tasks.filter(status=WorkflowTask.Status.OPEN).count(),
            "completed_tasks": completed_count,
            "average_completion_time": _format_duration(average_seconds),
        }

    def _document_intake(self, period_documents, start_date: date, today: date) -> list[dict]:
        counts = {
            row["day"]: row["count"]
            for row in period_documents.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        }
        values = []
        cursor = start_date
        while cursor <= today:
            values.append({"day": cursor, "count": counts.get(cursor, 0)})
            cursor += timedelta(days=1)
        maximum = max((item["count"] for item in values), default=0)
        for item in values:
            item["pct"] = _pct(item["count"], maximum)
        return values

    def _box_distribution(self, period_documents) -> list[dict]:
        rows = list(
            period_documents.values("space__name", "space__path")
            .annotate(count=Count("id"))
            .order_by("-count", "space__path")[:8]
        )
        maximum = max((row["count"] for row in rows), default=0)
        total = sum(row["count"] for row in rows)
        return [
            {
                "name": row["space__name"],
                "path": row["space__path"],
                "count": row["count"],
                "pct": _pct(row["count"], maximum),
                "share": round((row["count"] / total) * 100) if total else 0,
            }
            for row in rows
        ]

    def _workflow_status(self, workflow_instances) -> list[dict]:
        labels = dict(WorkflowInstance.Status.choices)
        counts = {
            row["status"]: row["count"]
            for row in workflow_instances.values("status").annotate(count=Count("id"))
        }
        maximum = max(counts.values(), default=0)
        return [
            {
                "status": status,
                "label": label,
                "count": counts.get(status, 0),
                "pct": _pct(counts.get(status, 0), maximum),
            }
            for status, label in labels.items()
        ]

    def _open_task_trend(
        self,
        workflow_tasks,
        start_date: date,
        today: date,
    ) -> list[dict]:
        values = []
        cursor = start_date
        while cursor <= today:
            end_at = _day_end(cursor)
            open_count = workflow_tasks.filter(created_at__lte=end_at).filter(
                status=WorkflowTask.Status.OPEN,
            ).count()
            completed_after_count = workflow_tasks.filter(
                created_at__lte=end_at,
                status=WorkflowTask.Status.COMPLETED,
                completed_at__gt=end_at,
            ).count()
            values.append(
                {
                    "day": cursor,
                    "count": open_count + completed_after_count,
                }
            )
            cursor += timedelta(days=1)
        maximum = max((item["count"] for item in values), default=0)
        for item in values:
            item["pct"] = _pct(item["count"], maximum)
        return values

    def _workflow_throughput(
        self,
        period_tasks,
        start_date: date,
        today: date,
    ) -> list[dict]:
        created_counts = {
            row["day"]: row["count"]
            for row in period_tasks.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"))
        }
        completed_counts = {
            row["day"]: row["count"]
            for row in WorkflowTask.objects.filter(
                tenant=self.tenant,
                status=WorkflowTask.Status.COMPLETED,
                completed_at__date__gte=start_date,
                completed_at__date__lte=today,
            )
            .annotate(day=TruncDate("completed_at"))
            .values("day")
            .annotate(count=Count("id"))
        }
        values = []
        cursor = start_date
        while cursor <= today:
            values.append(
                {
                    "day": cursor,
                    "created": created_counts.get(cursor, 0),
                    "completed": completed_counts.get(cursor, 0),
                }
            )
            cursor += timedelta(days=1)
        maximum = max(
            [item["created"] for item in values] + [item["completed"] for item in values],
            default=0,
        )
        for item in values:
            item["created_pct"] = _pct(item["created"], maximum)
            item["completed_pct"] = _pct(item["completed"], maximum)
        return values

    def _user_performance(self, completed_period_tasks) -> list[dict]:
        user_stats: dict[int | None, dict] = {}
        for task in completed_period_tasks:
            user_id = task.completed_by_id
            label = (
                task.completed_by.get_full_name()
                or task.completed_by.get_username()
                if task.completed_by
                else "Unbekannt"
            )
            stat = user_stats.setdefault(
                user_id,
                {
                    "label": label,
                    "count": 0,
                    "durations": [],
                },
            )
            stat["count"] += 1
            if task.completed_at:
                stat["durations"].append(
                    (task.completed_at - task.created_at).total_seconds()
                )

        memberships = TenantMembership.objects.filter(
            tenant=self.tenant,
            is_active=True,
        ).select_related("user")
        for membership in memberships:
            user_stats.setdefault(
                membership.user_id,
                {
                    "label": membership.user.get_full_name()
                    or membership.user.get_username(),
                    "count": 0,
                    "durations": [],
                },
            )

        rows = []
        maximum = max((stat["count"] for stat in user_stats.values()), default=0)
        for stat in user_stats.values():
            average_seconds = (
                sum(stat["durations"]) / len(stat["durations"])
                if stat["durations"]
                else None
            )
            rows.append(
                {
                    "label": stat["label"],
                    "count": stat["count"],
                    "pct": _pct(stat["count"], maximum),
                    "average_completion_time": _format_duration(average_seconds),
                }
            )
        return sorted(rows, key=lambda row: (-row["count"], row["label"]))[:10]

