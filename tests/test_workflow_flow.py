from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from doksio.accounts.models import Notification, TenantMembership, UserProfile
from doksio.accounts.services import EnsureDefaultTenantRoles
from doksio.audit.models import AuditEvent
from doksio.documents.services import CreateDocumentFromUpload, CreateDocumentSpace
from doksio.tenancy.models import Tenant
from doksio.workflows.models import (
    WorkflowInstance,
    WorkflowStep,
    WorkflowTask,
    WorkflowTemplate,
)
from doksio.workflows.services import (
    CompleteWorkflowTask,
    CreateWorkflowStep,
    CreateWorkflowTemplate,
    StartMatchingWorkflowsForDocument,
    StartWorkflowForDocument,
    UpdateWorkflowStep,
)


def _create_document(tenant, space, title="Invoice 4711"):
    document, _document_file = CreateDocumentFromUpload(
        tenant=tenant,
        title=title,
        space=space,
        file_obj=BytesIO(f"invoice content {space.path} {title}".encode()),
        original_filename=f"{title}.pdf",
        content_type="application/pdf",
    ).execute()
    return document


@pytest.mark.django_db
def test_start_workflow_creates_instance_and_first_task():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    step = CreateWorkflowStep(
        template=template,
        name="Sachlich prüfen",
        step_type="approval",
        assigned_role=roles["member"],
    ).execute()

    instance = StartWorkflowForDocument(
        template=template,
        document=document,
    ).execute()

    task = WorkflowTask.objects.get(instance=instance)
    assert instance.status == WorkflowInstance.Status.RUNNING
    assert instance.current_step == step
    assert task.title == "Sachlich prüfen"
    assert task.assigned_role == roles["member"]
    assert AuditEvent.objects.filter(event_type="workflow_instance.started").exists()


@pytest.mark.django_db
def test_complete_workflow_task_advances_to_next_step_and_completes_instance():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = _create_document(tenant, space)
    user = get_user_model().objects.create_user(username="alice")
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Prüfen",
        step_type="task",
        sort_order=10,
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Freigeben",
        step_type="approval",
        sort_order=20,
    ).execute()
    instance = StartWorkflowForDocument(template=template, document=document).execute()
    first_task = instance.tasks.get(status=WorkflowTask.Status.OPEN)

    CompleteWorkflowTask(task=first_task, actor=user).execute()

    instance.refresh_from_db()
    first_task.refresh_from_db()
    second_task = instance.tasks.get(status=WorkflowTask.Status.OPEN)
    assert first_task.status == WorkflowTask.Status.COMPLETED
    assert instance.status == WorkflowInstance.Status.RUNNING
    assert second_task.title == "Freigeben"

    CompleteWorkflowTask(task=second_task, actor=user).execute()

    instance.refresh_from_db()
    assert instance.status == WorkflowInstance.Status.COMPLETED
    assert instance.current_step is None
    assert AuditEvent.objects.filter(event_type="workflow_task.completed").count() == 2


@pytest.mark.django_db
def test_complete_workflow_task_requires_comment_when_configured():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = _create_document(tenant, space)
    user = get_user_model().objects.create_user(username="alice")
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Prüfen",
        step_type="task",
        comment_policy=WorkflowStep.CommentPolicy.REQUIRED,
    ).execute()
    instance = StartWorkflowForDocument(template=template, document=document).execute()
    task = instance.tasks.get()

    with pytest.raises(ValueError, match="requires a comment"):
        CompleteWorkflowTask(task=task, actor=user).execute()


@pytest.mark.django_db
def test_complete_workflow_task_ignores_comment_when_disabled():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = _create_document(tenant, space)
    user = get_user_model().objects.create_user(username="alice")
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Prüfen",
        step_type="task",
        comment_policy=WorkflowStep.CommentPolicy.DISABLED,
    ).execute()
    instance = StartWorkflowForDocument(template=template, document=document).execute()
    task = instance.tasks.get()

    CompleteWorkflowTask(
        task=task,
        actor=user,
        comment="Soll nicht gespeichert werden.",
    ).execute()

    task.refresh_from_db()
    assert task.status == WorkflowTask.Status.COMPLETED
    assert task.completion_comment == ""


@pytest.mark.django_db
def test_start_matching_workflows_for_document_uses_trigger_document_box():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    matching_box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    other_box = CreateDocumentSpace(
        tenant=tenant,
        name="Verträge",
        slug="vertraege",
    ).execute()
    matching_document = _create_document(tenant, matching_box)
    other_document = _create_document(tenant, other_box)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
        trigger_type=WorkflowTemplate.TriggerType.DOCUMENT_CREATED,
        trigger_document_space=matching_box,
    ).execute()
    CreateWorkflowStep(template=template, name="Prüfen", step_type="task").execute()

    instances = StartMatchingWorkflowsForDocument(
        document=matching_document,
    ).execute()
    other_instances = StartMatchingWorkflowsForDocument(
        document=other_document,
    ).execute()

    assert len(instances) == 1
    assert instances[0].template == template
    assert other_instances == []


@pytest.mark.django_db
def test_start_matching_workflows_for_document_can_include_child_boxes():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    parent_box = CreateDocumentSpace(
        tenant=tenant,
        name="Rechnungen",
        slug="rechnungen",
    ).execute()
    child_box = CreateDocumentSpace(
        tenant=tenant,
        parent=parent_box,
        name="Eingang",
        slug="eingang",
    ).execute()
    document = _create_document(tenant, child_box)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
        trigger_type=WorkflowTemplate.TriggerType.DOCUMENT_CREATED,
        trigger_document_space=parent_box,
        trigger_include_child_spaces=True,
    ).execute()
    CreateWorkflowStep(template=template, name="Prüfen", step_type="task").execute()

    instances = StartMatchingWorkflowsForDocument(document=document).execute()
    duplicate_instances = StartMatchingWorkflowsForDocument(document=document).execute()

    assert len(instances) == 1
    assert duplicate_instances == []
    assert (
        WorkflowInstance.objects.filter(document=document, template=template).count()
        == 1
    )


@pytest.mark.django_db
def test_document_creation_starts_matching_workflows_after_commit(
    django_capture_on_commit_callbacks,
):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    box = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
        trigger_type=WorkflowTemplate.TriggerType.DOCUMENT_CREATED,
        trigger_document_space=box,
    ).execute()
    CreateWorkflowStep(template=template, name="Prüfen", step_type="task").execute()

    with django_capture_on_commit_callbacks(execute=True):
        document = _create_document(tenant, box)

    assert WorkflowInstance.objects.filter(
        document=document,
        template=template,
        status=WorkflowInstance.Status.RUNNING,
    ).exists()


@pytest.mark.django_db
def test_workflow_settings_create_template_and_step(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["admin"],
    )
    client.force_login(user)

    response = client.post(
        reverse(
            "workflows:settings_template_create",
            kwargs={"tenant_slug": tenant.slug},
        ),
        {
            "name": "Rechnungsprüfung",
            "slug": "rechnungspruefung",
            "description": "",
            "trigger_type": WorkflowTemplate.TriggerType.MANUAL,
            "trigger_document_space": "",
            "trigger_include_child_spaces": "on",
            "is_active": "on",
        },
    )
    template = WorkflowTemplate.objects.get(tenant=tenant)
    assert response.status_code == 302

    response = client.post(
        reverse(
            "workflows:settings_step_create",
            kwargs={"tenant_slug": tenant.slug, "template_id": template.id},
        ),
        {
            "name": "Sachlich prüfen",
            "step_type": "approval",
            "assigned_role": roles["member"].id,
            "instructions": "Bitte prüfen.",
            "sort_order": "10",
            "comment_policy": WorkflowStep.CommentPolicy.REQUIRED,
        },
    )

    step = template.steps.get()
    assert response.status_code == 302
    assert step.name == "Sachlich prüfen"
    assert step.assigned_role == roles["member"]
    assert step.comment_policy == WorkflowStep.CommentPolicy.REQUIRED


@pytest.mark.django_db
def test_update_workflow_step_role_syncs_open_running_tasks():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    step = CreateWorkflowStep(
        template=template,
        name="Prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    StartWorkflowForDocument(template=template, document=document).execute()
    task = WorkflowTask.objects.get(step=step)
    assert task.assigned_role == roles["member"]

    UpdateWorkflowStep(
        step=step,
        name="Prüfen",
        step_type="task",
        assigned_role=roles["admin"],
        instructions="",
        sort_order=100,
        comment_policy=WorkflowStep.CommentPolicy.OPTIONAL,
    ).execute()

    task.refresh_from_db()
    assert task.assigned_role == roles["admin"]


@pytest.mark.django_db
def test_document_detail_can_start_and_complete_workflow(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {"action": "start_workflow", "template": template.id},
    )

    task = WorkflowTask.objects.get(document=document)
    assert response.status_code == 302
    assert task.status == WorkflowTask.Status.OPEN

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )
    content = response.content.decode()
    assert "Workflow" in content
    assert "1 laufend" in content
    assert "1 Aufgabe für dich" in content
    assert "document-task-box-open" in content
    assert "document-task-complete-button" in content
    assert "Aufgabe erledigen" in content

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "complete_workflow_task",
            "task_id": task.id,
            "comment": "Passt.",
        },
    )

    task.refresh_from_db()
    assert response.status_code == 302
    assert task.status == WorkflowTask.Status.COMPLETED


@pytest.mark.django_db
def test_document_detail_hides_disabled_workflow_comment_and_shows_log(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Rechnungsprüfung",
        slug="rechnung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
        comment_policy=WorkflowStep.CommentPolicy.DISABLED,
    ).execute()
    StartWorkflowForDocument(template=template, document=document).execute()
    task = WorkflowTask.objects.get(document=document)
    client.force_login(user)

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "workflow-task-comment" not in content

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "complete_workflow_task",
            "task_id": task.id,
            "comment": "Soll nicht gespeichert werden.",
        },
    )
    assert response.status_code == 302

    task.refresh_from_db()
    assert task.completion_comment == ""

    response = client.get(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        )
    )

    content = response.content.decode()
    assert "Dokumenten-Log" in content
    assert "Workflow-Schritt erledigt" in content
    assert "Rechnungsprüfung" in content
    assert "Sachlich prüfen" in content
    assert "alice" in content


@pytest.mark.django_db
def test_assigned_viewer_role_can_complete_workflow_task_without_workflow_use(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(
        username="viewer",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["viewer"],
    )
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Prüfung",
        slug="pruefung",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Sichtung",
        step_type="task",
        assigned_role=roles["viewer"],
    ).execute()
    StartWorkflowForDocument(template=template, document=document).execute()
    task = WorkflowTask.objects.get(document=document)
    client.force_login(user)

    response = client.post(
        reverse(
            "documents:detail",
            kwargs={"tenant_slug": tenant.slug, "document_id": document.id},
        ),
        {
            "action": "complete_workflow_task",
            "task_id": task.id,
            "comment": "Gesehen.",
        },
    )

    task.refresh_from_db()
    assert response.status_code == 302
    assert task.status == WorkflowTask.Status.COMPLETED


@pytest.mark.django_db
def test_dashboard_shows_my_open_workflow_tasks(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    visible_document = _create_document(tenant, space)
    hidden_document = _create_document(tenant, space, title="Invoice hidden")
    visible_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe",
    ).execute()
    hidden_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Admin-Prüfung",
        slug="admin-pruefung",
    ).execute()
    CreateWorkflowStep(
        template=visible_template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    CreateWorkflowStep(
        template=hidden_template,
        name="Administrativ prüfen",
        step_type="task",
        assigned_role=roles["admin"],
    ).execute()
    StartWorkflowForDocument(
        template=visible_template,
        document=visible_document,
    ).execute()
    StartWorkflowForDocument(
        template=hidden_template,
        document=hidden_document,
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    )

    tasks = list(response.context["workflow_tasks"])
    content = response.content.decode()
    assert response.status_code == 200
    assert [task.title for task in tasks] == ["Sachlich prüfen"]
    assert response.context["workflow_tasks_count"] == 1
    assert response.context["workflow_documents_count"] == 1
    assert response.context["sidebar_open_workflow_tasks_count"] == 1
    assert response.context["unread_notifications_count"] == 1
    assert "Meine Aufgaben" in content
    assert "workflow-task-row" in content
    assert "workflow-task-step" in content
    assert "workflow-task-filter" in content
    assert "Alle Workflows" in content
    assert "app-sidebar-count-badge" in content
    assert "app-account-notification-badge" in content
    assert "Benachrichtigungen" in content
    assert "1 offene Aufgaben" in content
    assert "1 Dokument zu bearbeiten" in content
    assert "Workflow offen 0/1" in content
    assert "Sachlich prüfen" in content
    assert "Administrativ prüfen" not in content


@pytest.mark.django_db
def test_dashboard_filters_my_open_workflow_tasks_by_workflow(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    approval_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe",
    ).execute()
    review_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Nachprüfung",
        slug="nachpruefung",
    ).execute()
    CreateWorkflowStep(
        template=approval_template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    CreateWorkflowStep(
        template=review_template,
        name="Nachprüfung erledigen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    StartWorkflowForDocument(
        template=approval_template,
        document=_create_document(tenant, space, title="Rechnung Freigabe"),
    ).execute()
    StartWorkflowForDocument(
        template=review_template,
        document=_create_document(tenant, space, title="Rechnung Nachprüfung"),
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug}),
        {"workflow": review_template.id},
    )

    tasks = list(response.context["workflow_tasks"])
    content = response.content.decode()
    assert response.status_code == 200
    assert response.context["selected_workflow_id"] == review_template.id
    assert [task.title for task in tasks] == ["Nachprüfung erledigen"]
    assert "workflow-task-filter" in content
    assert "Freigabe" in content
    assert "Nachprüfung" in content
    assert "Sachlich prüfen" not in content
    assert "Nachprüfung erledigen" in content


@pytest.mark.django_db
def test_workflow_task_creation_creates_in_app_notification_for_visible_member():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    member = get_user_model().objects.create_user(username="alice")
    admin = get_user_model().objects.create_user(username="admin")
    TenantMembership.objects.create(
        tenant=tenant,
        user=member,
        role=roles["member"],
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin,
        role=roles["admin"],
    )
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe-notification",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()

    StartWorkflowForDocument(template=template, document=document).execute()

    notification = Notification.objects.get(
        recipient=member,
        notification_type=Notification.Type.WORKFLOW_TASK_CREATED,
    )
    assert notification.tenant == tenant
    assert notification.document == document
    assert notification.workflow_task.title == "Sachlich prüfen"
    assert notification.notification_type == Notification.Type.WORKFLOW_TASK_CREATED
    assert notification.title == "Neue Workflow-Aufgabe"
    assert "Sachlich prüfen" in notification.body
    assert str(document.id) in notification.link_url
    assert not Notification.objects.filter(
        recipient=admin,
        notification_type=Notification.Type.WORKFLOW_TASK_CREATED,
    ).exists()


@pytest.mark.django_db
def test_workflow_start_creates_notification_for_workflow_user():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    admin = get_user_model().objects.create_user(username="admin")
    TenantMembership.objects.create(
        tenant=tenant,
        user=admin,
        role=roles["admin"],
    )
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe-start-notification",
    ).execute()

    StartWorkflowForDocument(template=template, document=document).execute()

    notification = Notification.objects.get(
        recipient=admin,
        notification_type=Notification.Type.WORKFLOW_STARTED,
    )
    assert notification.title == "Workflow gestartet"
    assert "Freigabe" in notification.body
    assert notification.document == document


@pytest.mark.django_db
def test_workflow_task_notification_respects_disabled_user_notifications():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    member = get_user_model().objects.create_user(username="alice")
    UserProfile.objects.create(user=member, notifications_enabled=False)
    TenantMembership.objects.create(
        tenant=tenant,
        user=member,
        role=roles["member"],
    )
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe-notification-disabled",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()

    StartWorkflowForDocument(template=template, document=document).execute()

    assert not Notification.objects.filter(recipient=member).exists()


@pytest.mark.django_db
def test_workflow_task_notification_respects_disabled_workflow_notifications():
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    member = get_user_model().objects.create_user(username="alice")
    UserProfile.objects.create(
        user=member,
        notifications_enabled=True,
        workflow_notifications_enabled=False,
        mention_notifications_enabled=True,
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=member,
        role=roles["member"],
    )
    document = _create_document(tenant, space)
    template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe-workflow-notification-disabled",
    ).execute()
    CreateWorkflowStep(
        template=template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()

    StartWorkflowForDocument(template=template, document=document).execute()

    assert not Notification.objects.filter(recipient=member).exists()


@pytest.mark.django_db
def test_task_list_shows_my_open_workflow_tasks_paginated(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    visible_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe",
    ).execute()
    hidden_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Admin-Prüfung",
        slug="admin-pruefung",
    ).execute()
    CreateWorkflowStep(
        template=visible_template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    CreateWorkflowStep(
        template=hidden_template,
        name="Administrativ prüfen",
        step_type="task",
        assigned_role=roles["admin"],
    ).execute()
    for index in range(30):
        StartWorkflowForDocument(
            template=visible_template,
            document=_create_document(tenant, space, title=f"Rechnung {index}"),
        ).execute()
    StartWorkflowForDocument(
        template=hidden_template,
        document=_create_document(tenant, space, title="Admin-Rechnung"),
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:tasks", kwargs={"tenant_slug": tenant.slug}),
        {"page": "2"},
    )

    tasks = list(response.context["workflow_tasks"])
    content = response.content.decode()
    assert response.status_code == 200
    assert len(tasks) == 5
    assert response.context["workflow_tasks_count"] == 30
    assert response.context["workflow_documents_count"] == 30
    assert "Meine Aufgaben" in content
    assert "30 offene Aufgaben" in content
    assert "workflow-task-row" in content
    assert "workflow-task-step" in content
    assert "Sachlich prüfen" in content
    assert "Administrativ prüfen" not in content
    assert "page=1" in content


@pytest.mark.django_db
def test_task_list_filters_my_open_workflow_tasks_by_workflow(client):
    tenant = Tenant.objects.create(name="Acme GmbH", slug="acme")
    roles = EnsureDefaultTenantRoles(tenant=tenant).execute()
    space = CreateDocumentSpace(tenant=tenant, name="Rechnungen").execute()
    user = get_user_model().objects.create_user(
        username="alice",
        password="secret",
    )
    TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=roles["member"],
    )
    approval_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Freigabe",
        slug="freigabe",
    ).execute()
    review_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Nachprüfung",
        slug="nachpruefung",
    ).execute()
    hidden_template = CreateWorkflowTemplate(
        tenant=tenant,
        name="Admin-Prüfung",
        slug="admin-pruefung-filter",
    ).execute()
    CreateWorkflowStep(
        template=approval_template,
        name="Sachlich prüfen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    CreateWorkflowStep(
        template=review_template,
        name="Nachprüfung erledigen",
        step_type="task",
        assigned_role=roles["member"],
    ).execute()
    CreateWorkflowStep(
        template=hidden_template,
        name="Administrativ prüfen",
        step_type="task",
        assigned_role=roles["admin"],
    ).execute()
    StartWorkflowForDocument(
        template=approval_template,
        document=_create_document(tenant, space, title="Rechnung Freigabe"),
    ).execute()
    StartWorkflowForDocument(
        template=review_template,
        document=_create_document(tenant, space, title="Rechnung Nachprüfung"),
    ).execute()
    StartWorkflowForDocument(
        template=hidden_template,
        document=_create_document(tenant, space, title="Rechnung Admin"),
    ).execute()
    client.force_login(user)

    response = client.get(
        reverse("documents:tasks", kwargs={"tenant_slug": tenant.slug}),
        {"workflow": review_template.id},
    )

    tasks = list(response.context["workflow_tasks"])
    content = response.content.decode()
    assert response.status_code == 200
    assert response.context["selected_workflow_id"] == review_template.id
    assert response.context["workflow_tasks_count"] == 1
    assert [task.title for task in tasks] == ["Nachprüfung erledigen"]
    assert "workflow-task-filter" in content
    assert "Freigabe" in content
    assert "Nachprüfung" in content
    assert "Admin-Prüfung" not in content
    assert "Sachlich prüfen" not in content
    assert "Nachprüfung erledigen" in content
