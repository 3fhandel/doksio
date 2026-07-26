from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from doksio.documents.models import DocumentBoxScanOptimizationJob
from doksio.documents.services import CreateDocumentSpace
from doksio.project.background_jobs import (
    cancel_background_job,
    tenant_background_jobs,
)
from doksio.tenancy.models import Tenant


@pytest.mark.django_db
def test_database_jobs_remain_visible_when_worker_is_unavailable(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    job = DocumentBoxScanOptimizationJob.objects.create(
        tenant=tenant,
        document_space=space,
        total_documents=100,
        processed_documents=12,
    )

    def unavailable_inspector(*_args, **_kwargs):
        raise RuntimeError("Redis nicht erreichbar")

    monkeypatch.setattr(
        "doksio.project.background_jobs.app.control.inspect",
        unavailable_inspector,
    )

    jobs, worker_error = tenant_background_jobs(tenant)

    assert worker_error == "Redis nicht erreichbar"
    assert len(jobs) == 1
    assert jobs[0].job_type == "scan"
    assert jobs[0].object_id == job.id
    assert jobs[0].state == "queued"
    assert jobs[0].progress == "12/100"


@pytest.mark.django_db
def test_queued_database_job_can_be_cancelled_without_celery_task_id():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    job = DocumentBoxScanOptimizationJob.objects.create(
        tenant=tenant,
        document_space=space,
        total_documents=100,
    )

    title = cancel_background_job(
        tenant=tenant,
        job_type="scan",
        object_id=job.id,
        task_id="",
    )

    job.refresh_from_db()
    assert title == f"Scan-Speicher optimieren: {space.path}"
    assert job.status == DocumentBoxScanOptimizationJob.Status.FAILED
    assert job.completed_at is not None
    assert job.error_message == "Durch einen Administrator abgebrochen."


@pytest.mark.django_db
def test_stale_running_maintenance_job_is_shown_as_interrupted(monkeypatch):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    job = DocumentBoxScanOptimizationJob.objects.create(
        tenant=tenant,
        document_space=space,
        status=DocumentBoxScanOptimizationJob.Status.RUNNING,
        total_documents=100,
        processed_documents=12,
        heartbeat_at=timezone.now() - timedelta(minutes=10),
        lease_expires_at=timezone.now() - timedelta(minutes=5),
    )
    monkeypatch.setattr(
        "doksio.project.background_jobs.app.control.inspect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Redis nicht erreichbar")
        ),
    )

    jobs, _worker_error = tenant_background_jobs(tenant)

    visible_job = next(item for item in jobs if item.object_id == job.id)
    assert visible_job.state == "interrupted"
    assert visible_job.can_cancel is True
