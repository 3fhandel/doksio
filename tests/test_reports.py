from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from doksio.accounts.models import TenantMembership
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.tenancy.models import Tenant
from doksio.workflows.models import WorkflowInstance, WorkflowTask
from doksio.workflows.services import CreateWorkflowStep, CreateWorkflowTemplate


@pytest.mark.django_db
def test_reports_overview_shows_controlling_metrics_for_admin(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    invoice_box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    personnel_box = CreateDocumentSpace(
        tenant=tenant,
        name="Personal",
        slug="personal",
    ).execute()
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    admin = get_user_model().objects.create_user(
        username="admin",
        password="secret",
    )
    processor = get_user_model().objects.create_user(
        username="sachbearbeiter",
        password="secret",
        first_name="Sabine",
        last_name="Prüfer",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin,
        role=roles["admin"],
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=processor,
        role=roles["member"],
    )
    first_document, _first_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Rechnung 1",
        space=invoice_box,
        file_obj=BytesIO(b"invoice 1"),
        original_filename="invoice-1.pdf",
        content_type="application/pdf",
        created_by=admin,
        auto_start_ocr=False,
    ).execute()
    second_document, _second_file = CreateDocumentFromUpload(
        tenant=tenant,
        title="Personalakte",
        space=personnel_box,
        file_obj=BytesIO(b"personnel"),
        original_filename="personnel.pdf",
        content_type="application/pdf",
        created_by=admin,
        auto_start_ocr=False,
    ).execute()
    workflow_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnungspruefung",
    ).execute()
    workflow_step = CreateWorkflowStep(
        template=workflow_template,
        name="Prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    running_instance = WorkflowInstance.objects.create(
        tenant=tenant,
        template=workflow_template,
        document=first_document,
        status=WorkflowInstance.Status.RUNNING,
        current_step=workflow_step,
        started_by=admin,
    )
    WorkflowTask.objects.create(
        tenant=tenant,
        instance=running_instance,
        step=workflow_step,
        document=first_document,
        title="Rechnung prüfen",
        status=WorkflowTask.Status.OPEN,
        assigned_role=roles["member"],
    )
    completed_instance = WorkflowInstance.objects.create(
        tenant=tenant,
        template=workflow_template,
        document=second_document,
        status=WorkflowInstance.Status.COMPLETED,
        completed_at=timezone.now(),
        started_by=admin,
    )
    completed_task = WorkflowTask.objects.create(
        tenant=tenant,
        instance=completed_instance,
        step=workflow_step,
        document=second_document,
        title="Personalakte prüfen",
        status=WorkflowTask.Status.COMPLETED,
        assigned_role=roles["member"],
        completed_by=processor,
        completed_at=timezone.now(),
    )
    WorkflowTask.objects.filter(id=completed_task.id).update(
        created_at=timezone.now() - timedelta(hours=2),
    )
    client.force_login(admin)

    response = client.get(
        reverse("reports:overview", kwargs={"tenant_slug": tenant.slug}),
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Auswertungen" in content
    assert "Neue Dokumente" in content
    assert "Verteilung auf Boxen" in content
    assert "Workflow-Status" in content
    assert "Offene Aufgaben im Zeitverlauf" in content
    assert "Abarbeitung nach Benutzer" in content
    assert "Rechnungen" in content
    assert "Personal" in content
    assert "Sabine Prüfer" in content
    assert "href=\"/t/acme/reports/\"" in content


@pytest.mark.django_db
def test_reports_overview_requires_reports_permission(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="viewer",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )
    client.force_login(user)

    response = client.get(
        reverse("reports:overview", kwargs={"tenant_slug": tenant.slug}),
    )

    assert response.status_code == 403

