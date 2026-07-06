from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from domasy.accounts.forms import (
    TenantMembershipCreateForm,
    TenantMembershipUpdateForm,
    TenantRoleCreateForm,
    TenantRoleUpdateForm,
)
from domasy.accounts.models import TenantMembership, TenantRole
from domasy.accounts.services import (
    AddTenantMember,
    CreateTenantRole,
    UpdateTenantMembership,
    UpdateTenantRole,
)
from domasy.audit.models import AuditEvent
from domasy.documents.forms import (
    DocumentCommentForm,
    DocumentCoreMetadataForm,
    DocumentMetadataFieldForm,
    DocumentMetadataForm,
    DocumentSpaceForm,
    DocumentSpaceUpdateForm,
    DocumentTagForm,
    DocumentUploadForm,
)
from domasy.documents.models import (
    Document,
    DocumentFile,
    DocumentMetadataField,
    DocumentSpace,
)
from domasy.documents.policies import (
    can_administer_tenant,
    can_download_document_file,
    can_manage_document_spaces,
    can_manage_members,
    can_manage_roles,
    can_upload_document,
    can_view_document,
    filter_documents_for_user,
)
from domasy.documents.services import (
    AddDocumentComment,
    CreateDocumentFromUpload,
    CreateDocumentMetadataField,
    CreateDocumentSpace,
    SetDocumentTags,
    UpdateDocumentCoreMetadata,
    UpdateDocumentMetadata,
    UpdateDocumentMetadataField,
    UpdateDocumentSpace,
)
from domasy.ocr.services import StartOcrForDocumentFile
from domasy.pagination import paginate_queryset
from domasy.tenancy.services import get_default_tenant_for_user, get_tenant_for_user
from domasy.workflows.forms import CompleteWorkflowTaskForm, StartWorkflowForm
from domasy.workflows.models import WorkflowInstance, WorkflowTask, WorkflowTemplate
from domasy.workflows.policies import (
    can_complete_workflow_task,
    can_use_workflows,
    filter_workflow_tasks_for_user,
)
from domasy.workflows.services import CompleteWorkflowTask, StartWorkflowForDocument

DOCUMENT_LOG_EVENT_LABELS = {
    "document.created": "Dokument erstellt",
    "document_file.stored": "Datei gespeichert",
    "document_core_metadata.updated": "Kerndaten aktualisiert",
    "document_metadata.updated": "Metadaten aktualisiert",
    "document_comment.created": "Kommentar hinzugefügt",
    "document_tags.updated": "Tags aktualisiert",
    "workflow_instance.started": "Workflow gestartet",
    "workflow_task.completed": "Workflow-Schritt erledigt",
}

PDF_PREVIEW_CONTENT_TYPES = {"application/pdf"}
IMAGE_PREVIEW_CONTENT_TYPES = {
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _tenant_login_redirect(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_url = reverse("accounts:tenant_login", kwargs={"tenant_slug": tenant_slug})
    return redirect(f"{login_url}?next={request.get_full_path()}")


def _system_login_redirect(request: HttpRequest) -> HttpResponse:
    login_url = reverse("accounts:system_login")
    return redirect(f"{login_url}?next={request.get_full_path()}")


def _document_log_entries(document: Document):
    events = (
        AuditEvent.objects.filter(tenant=document.tenant)
        .filter(
            Q(object_type="documents.Document", object_id=str(document.id))
            | Q(data__document_id=document.id)
        )
        .select_related("actor")
        .order_by("-created_at", "-id")
    )
    return [
        {
            "event": event,
            "label": DOCUMENT_LOG_EVENT_LABELS.get(event.event_type, event.event_type),
        }
        for event in events
    ]


def _document_preview(document: Document) -> tuple[DocumentFile | None, str]:
    pdf_file = (
        document.files.filter(content_type__in=PDF_PREVIEW_CONTENT_TYPES)
        .order_by("file_kind", "-version", "-created_at")
        .first()
    )
    if pdf_file is not None:
        return pdf_file, "pdf"

    image_file = (
        document.files.filter(content_type__in=IMAGE_PREVIEW_CONTENT_TYPES)
        .order_by("file_kind", "-version", "-created_at")
        .first()
    )
    if image_file is not None:
        return image_file, "image"
    return None, ""


def dashboard_redirect(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return _system_login_redirect(request)

    tenant = get_default_tenant_for_user(request.user)
    if tenant is None:
        return render(
            request,
            "documents/dashboard.html",
            {
                "tenant": None,
                "documents": Document.objects.none(),
                "documents_count": 0,
                "documents_page_obj": None,
                "workflow_tasks": WorkflowTask.objects.none(),
                "workflow_tasks_count": 0,
                "workflow_documents_count": 0,
                "workflow_tasks_page_obj": None,
            },
        )
    return redirect("documents:dashboard", tenant_slug=tenant.slug)


def dashboard(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    documents_queryset = filter_documents_for_user(
        Document.objects.filter(tenant=tenant)
        .select_related("space")
        .prefetch_related("files")
        .order_by("-created_at", "-id"),
        request.user,
        tenant,
    )
    documents_page_obj = paginate_queryset(
        request,
        documents_queryset,
        page_param="uploads_page",
        per_page=10,
    )
    workflow_tasks_queryset = filter_workflow_tasks_for_user(
        WorkflowTask.objects.filter(
            tenant=tenant,
            status=WorkflowTask.Status.OPEN,
        )
        .select_related(
            "assigned_role",
            "document",
            "document__space",
            "instance__template",
            "step",
        )
        .order_by("created_at", "id"),
        request.user,
        tenant,
    )
    workflow_tasks_page_obj = paginate_queryset(
        request,
        workflow_tasks_queryset,
        page_param="tasks_page",
        per_page=10,
    )
    workflow_documents_count = (
        workflow_tasks_queryset.order_by()
        .values("document_id")
        .distinct()
        .count()
    )
    return render(
        request,
        "documents/dashboard.html",
        {
            "tenant": tenant,
            "documents": documents_page_obj.object_list,
            "documents_count": documents_page_obj.paginator.count,
            "documents_page_obj": documents_page_obj,
            "workflow_tasks": workflow_tasks_page_obj.object_list,
            "workflow_tasks_count": workflow_tasks_page_obj.paginator.count,
            "workflow_documents_count": workflow_documents_count,
            "workflow_tasks_page_obj": workflow_tasks_page_obj,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_list(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    documents_queryset = filter_documents_for_user(
        Document.objects.filter(tenant=tenant)
        .select_related("space")
        .prefetch_related("files")
        .order_by("-created_at", "-id"),
        request.user,
        tenant,
    )
    documents_page_obj = paginate_queryset(
        request,
        documents_queryset,
        per_page=25,
    )
    return render(
        request,
        "documents/document_list.html",
        {
            "tenant": tenant,
            "documents": documents_page_obj.object_list,
            "documents_count": documents_page_obj.paginator.count,
            "documents_page_obj": documents_page_obj,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_upload(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    if not can_upload_document(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentUploadForm(
            request.POST,
            request.FILES,
            tenant=tenant,
            user=request.user,
        )
        if form.is_valid():
            uploaded_file = form.cleaned_data["file"]
            document, _document_file = CreateDocumentFromUpload(
                tenant=tenant,
                title=form.cleaned_data["title"],
                space=form.cleaned_data["space"],
                file_obj=uploaded_file,
                original_filename=uploaded_file.name,
                content_type=uploaded_file.content_type or "application/octet-stream",
                created_by=request.user,
            ).execute()
            messages.success(request, "Dokument wurde gespeichert.")
            return redirect(
                "documents:detail",
                tenant_slug=tenant.slug,
                document_id=document.id,
            )
    else:
        form = DocumentUploadForm(tenant=tenant, user=request.user)

    return render(
        request,
        "documents/document_upload.html",
        {
            "tenant": tenant,
            "form": form,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_detail(
    request: HttpRequest,
    tenant_slug: str,
    document_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document_queryset = Document.objects.select_related("space").prefetch_related(
        "files",
        "files__ocr_jobs",
        "comments__created_by",
        "tag_assignments__tag",
        "space__metadata_fields",
    )
    document = get_object_or_404(
        document_queryset,
        id=document_id,
        tenant=tenant,
    )
    if not can_view_document(request.user, document):
        raise PermissionDenied

    comment_form = DocumentCommentForm()
    metadata_fields = document.space.metadata_fields.filter(
        is_active=True,
    ).order_by("sort_order", "name")
    metadata_form = DocumentMetadataForm(
        metadata_fields=metadata_fields,
        metadata=document.metadata,
    )
    tag_form = DocumentTagForm(
        tenant=tenant,
        initial={
            "tag_names": ", ".join(
                assignment.tag.name for assignment in document.tag_assignments.all()
            )
        },
    )
    start_workflow_form = StartWorkflowForm(tenant=tenant)
    complete_workflow_task_form = CompleteWorkflowTaskForm()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_comment":
            comment_form = DocumentCommentForm(request.POST)
            if comment_form.is_valid():
                AddDocumentComment(
                    document=document,
                    body=comment_form.cleaned_data["body"],
                    actor=request.user,
                ).execute()
                messages.success(request, "Kommentar wurde hinzugefügt.")
                return redirect(
                    "documents:detail",
                    tenant_slug=tenant.slug,
                    document_id=document.id,
                )
        elif action == "update_tags":
            tag_form = DocumentTagForm(request.POST, tenant=tenant)
            if tag_form.is_valid():
                SetDocumentTags(
                    document=document,
                    tag_names=tag_form.cleaned_data["tag_names"],
                    actor=request.user,
                ).execute()
                messages.success(request, "Tags wurden aktualisiert.")
                return redirect(
                    "documents:detail",
                    tenant_slug=tenant.slug,
                    document_id=document.id,
                )
        elif action == "update_metadata":
            metadata_form = DocumentMetadataForm(
                request.POST,
                metadata_fields=metadata_fields,
                metadata=document.metadata,
            )
            if metadata_form.is_valid():
                UpdateDocumentMetadata(
                    document=document,
                    metadata=metadata_form.cleaned_metadata(),
                    actor=request.user,
                ).execute()
                messages.success(request, "Metadaten wurden aktualisiert.")
                return redirect(
                    "documents:detail",
                    tenant_slug=tenant.slug,
                    document_id=document.id,
                )
        elif action == "start_ocr":
            if not can_upload_document(request.user, tenant):
                raise PermissionDenied
            document_file = get_object_or_404(
                DocumentFile,
                id=request.POST.get("file_id"),
                tenant=tenant,
                document=document,
            )
            StartOcrForDocumentFile(
                document_file=document_file,
                actor=request.user,
            ).execute()
            messages.success(request, "OCR wurde gestartet.")
            return redirect(
                "documents:detail",
                tenant_slug=tenant.slug,
                document_id=document.id,
            )
        elif action == "start_workflow":
            if not can_use_workflows(request.user, tenant):
                raise PermissionDenied
            start_workflow_form = StartWorkflowForm(request.POST, tenant=tenant)
            if start_workflow_form.is_valid():
                StartWorkflowForDocument(
                    template=start_workflow_form.cleaned_data["template"],
                    document=document,
                    actor=request.user,
                ).execute()
                messages.success(request, "Workflow wurde gestartet.")
                return redirect(
                    "documents:detail",
                    tenant_slug=tenant.slug,
                    document_id=document.id,
                )
        elif action == "complete_workflow_task":
            complete_workflow_task_form = CompleteWorkflowTaskForm(request.POST)
            if complete_workflow_task_form.is_valid():
                task = get_object_or_404(
                    WorkflowTask.objects.select_related(
                        "tenant",
                        "document",
                        "step",
                        "instance",
                        "assigned_role",
                    ),
                    id=complete_workflow_task_form.cleaned_data["task_id"],
                    tenant=tenant,
                    document=document,
                    status=WorkflowTask.Status.OPEN,
                )
                if not can_complete_workflow_task(request.user, task):
                    raise PermissionDenied
                CompleteWorkflowTask(
                    task=task,
                    actor=request.user,
                    comment=complete_workflow_task_form.cleaned_data["comment"],
                ).execute()
                messages.success(request, "Workflow-Aufgabe wurde erledigt.")
                return redirect(
                    "documents:detail",
                    tenant_slug=tenant.slug,
                    document_id=document.id,
                )

    preview_file, preview_kind = _document_preview(document)
    preview_ocr_job = preview_file.latest_ocr_job if preview_file is not None else None
    workflow_instances = list(
        document.workflow_instances.select_related(
            "template",
            "current_step",
        ).prefetch_related("tasks__step", "tasks__assigned_role")
    )
    open_workflow_instances = [
        instance
        for instance in workflow_instances
        if instance.status == WorkflowInstance.Status.RUNNING
    ]
    open_workflow_tasks_queryset = document.workflow_tasks.filter(
        status=WorkflowTask.Status.OPEN,
    ).select_related("step", "assigned_role", "instance__template")
    open_workflow_tasks = [
        task
        for task in open_workflow_tasks_queryset
        if can_complete_workflow_task(request.user, task)
    ]
    workflow_templates_available = WorkflowTemplate.objects.filter(
        tenant=tenant,
        is_active=True,
        trigger_type=WorkflowTemplate.TriggerType.MANUAL,
    ).exists()
    comments = list(document.comments.all())

    return render(
        request,
        "documents/document_detail.html",
        {
            "tenant": tenant,
            "document": document,
            "preview_file": preview_file,
            "preview_kind": preview_kind,
            "preview_ocr_job": preview_ocr_job,
            "comment_form": comment_form,
            "metadata_form": metadata_form,
            "tag_form": tag_form,
            "start_workflow_form": start_workflow_form,
            "complete_workflow_task_form": complete_workflow_task_form,
            "workflow_instances": workflow_instances,
            "open_workflow_instances": open_workflow_instances,
            "open_workflow_tasks": open_workflow_tasks,
            "workflow_templates_available": workflow_templates_available,
            "comments": comments,
            "comments_count": len(comments),
            "latest_comment": comments[-1] if comments else None,
            "document_log_entries": _document_log_entries(document),
            "can_use_workflows": can_use_workflows(request.user, tenant),
            "can_start_ocr": can_upload_document(request.user, tenant),
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_core_metadata_edit(
    request: HttpRequest,
    tenant_slug: str,
    document_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document = get_object_or_404(
        Document.objects.select_related("space"),
        id=document_id,
        tenant=tenant,
    )
    if not can_view_document(request.user, document):
        raise PermissionDenied

    form = DocumentCoreMetadataForm(
        request.POST or None,
        initial={
            "title": document.title,
            "document_date": document.document_date,
        },
    )
    if request.method == "POST" and form.is_valid():
        UpdateDocumentCoreMetadata(
            document=document,
            title=form.cleaned_data["title"],
            document_date=form.cleaned_data["document_date"],
            actor=request.user,
        ).execute()
        messages.success(request, "Kerndaten wurden aktualisiert.")
        return redirect(
            "documents:detail",
            tenant_slug=tenant.slug,
            document_id=document.id,
        )

    return render(
        request,
        "documents/document_core_metadata_form.html",
        {
            "tenant": tenant,
            "document": document,
            "form": form,
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def document_file_download(
    request: HttpRequest,
    tenant_slug: str,
    file_id: int,
) -> FileResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    document_file = get_object_or_404(
        DocumentFile.objects.select_related("document", "tenant"),
        id=file_id,
        tenant=tenant,
    )
    if not can_download_document_file(request.user, document_file):
        raise PermissionDenied

    file_handle = default_storage.open(document_file.storage_key, "rb")
    return FileResponse(
        file_handle,
        as_attachment=request.GET.get("inline") != "1",
        filename=document_file.original_filename,
        content_type=document_file.content_type,
    )


def index(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("documents:dashboard_redirect")
    login_url = reverse("accounts:system_login")
    next_url = reverse("documents:dashboard_redirect")
    return redirect(f"{login_url}?next={next_url}")


def tenant_settings_document_boxes(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    spaces = DocumentSpace.objects.filter(tenant=tenant).order_by("path")
    return render(
        request,
        "documents/settings_document_boxes.html",
        {
            "tenant": tenant,
            "spaces": spaces,
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_document_box_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = DocumentSpaceForm(request.POST, tenant=tenant)
        if form.is_valid():
            CreateDocumentSpace(
                tenant=tenant,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                parent=form.cleaned_data["parent"],
                description=form.cleaned_data["description"],
                review_assist_enabled=form.cleaned_data["review_assist_enabled"],
            ).execute()
            messages.success(request, "Dokumentenbox wurde erstellt.")
            return redirect(
                "documents:settings_document_boxes",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentSpaceForm(tenant=tenant)

    return render(
        request,
        "documents/settings_document_box_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Dokumentenbox erstellen",
            "submit_label": "Box erstellen",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_document_box_edit(
    request: HttpRequest,
    tenant_slug: str,
    box_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    document_space = get_object_or_404(DocumentSpace, id=box_id, tenant=tenant)

    if request.method == "POST":
        form = DocumentSpaceUpdateForm(
            request.POST,
            tenant=tenant,
            document_space=document_space,
        )
        if form.is_valid():
            UpdateDocumentSpace(
                document_space=document_space,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                parent=form.cleaned_data["parent"],
                description=form.cleaned_data["description"],
                review_assist_enabled=form.cleaned_data["review_assist_enabled"],
                is_active=form.cleaned_data["is_active"],
            ).execute()
            messages.success(request, "Dokumentenbox wurde aktualisiert.")
            return redirect(
                "documents:settings_document_boxes",
                tenant_slug=tenant.slug,
            )
    else:
        form = DocumentSpaceUpdateForm(
            tenant=tenant,
            document_space=document_space,
            initial={
                "name": document_space.name,
                "slug": document_space.slug,
                "parent": document_space.parent_id,
                "description": document_space.description,
                "review_assist_enabled": document_space.review_assist_enabled,
                "is_active": document_space.is_active,
            },
        )

    return render(
        request,
        "documents/settings_document_box_form.html",
        {
            "tenant": tenant,
            "form": form,
            "document_space": document_space,
            "metadata_fields": document_space.metadata_fields.order_by(
                "sort_order",
                "name",
            ),
            "form_title": "Dokumentenbox bearbeiten",
            "submit_label": "Box speichern",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_metadata_field_create(
    request: HttpRequest,
    tenant_slug: str,
    box_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    document_space = get_object_or_404(DocumentSpace, id=box_id, tenant=tenant)
    if request.method == "POST":
        form = DocumentMetadataFieldForm(
            request.POST,
            tenant=tenant,
            document_space=document_space,
        )
        if form.is_valid():
            CreateDocumentMetadataField(
                tenant=tenant,
                space=document_space,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                field_type=form.cleaned_data["field_type"],
                help_text=form.cleaned_data["help_text"],
                choices=form.cleaned_data["choices"],
                sort_order=form.cleaned_data["sort_order"],
                is_required=form.cleaned_data["is_required"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Metadatenfeld wurde erstellt.")
            return redirect(
                "documents:settings_document_box_edit",
                tenant_slug=tenant.slug,
                box_id=document_space.id,
            )
    else:
        form = DocumentMetadataFieldForm(
            tenant=tenant,
            document_space=document_space,
        )

    return render(
        request,
        "documents/settings_metadata_field_form.html",
        {
            "tenant": tenant,
            "document_space": document_space,
            "form": form,
            "form_title": "Metadatenfeld erstellen",
            "submit_label": "Feld erstellen",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_metadata_field_edit(
    request: HttpRequest,
    tenant_slug: str,
    box_id: int,
    field_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_document_spaces(request.user, tenant):
        raise PermissionDenied

    document_space = get_object_or_404(DocumentSpace, id=box_id, tenant=tenant)
    metadata_field = get_object_or_404(
        DocumentMetadataField,
        id=field_id,
        tenant=tenant,
        space=document_space,
    )
    if request.method == "POST":
        form = DocumentMetadataFieldForm(
            request.POST,
            tenant=tenant,
            document_space=document_space,
            metadata_field=metadata_field,
        )
        if form.is_valid():
            UpdateDocumentMetadataField(
                metadata_field=metadata_field,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                field_type=form.cleaned_data["field_type"],
                help_text=form.cleaned_data["help_text"],
                choices=form.cleaned_data["choices"],
                sort_order=form.cleaned_data["sort_order"],
                is_required=form.cleaned_data["is_required"],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Metadatenfeld wurde aktualisiert.")
            return redirect(
                "documents:settings_document_box_edit",
                tenant_slug=tenant.slug,
                box_id=document_space.id,
            )
    else:
        form = DocumentMetadataFieldForm(
            tenant=tenant,
            document_space=document_space,
            metadata_field=metadata_field,
            initial={
                "name": metadata_field.name,
                "slug": metadata_field.slug,
                "field_type": metadata_field.field_type,
                "help_text": metadata_field.help_text,
                "choices_text": "\n".join(metadata_field.choices),
                "sort_order": metadata_field.sort_order,
                "is_required": metadata_field.is_required,
                "is_active": metadata_field.is_active,
            },
        )

    return render(
        request,
        "documents/settings_metadata_field_form.html",
        {
            "tenant": tenant,
            "document_space": document_space,
            "metadata_field": metadata_field,
            "form": form,
            "form_title": "Metadatenfeld bearbeiten",
            "submit_label": "Feld speichern",
            "active_settings_section": "document_boxes",
        },
    )


def tenant_settings_overview(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_administer_tenant(request.user, tenant):
        raise PermissionDenied

    return render(
        request,
        "documents/settings_overview.html",
        {
            "tenant": tenant,
            "active_settings_section": "overview",
        },
    )


def tenant_settings_members(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_members(request.user, tenant):
        raise PermissionDenied

    memberships = (
        TenantMembership.objects.select_related("user", "tenant", "role")
        .prefetch_related("roles")
        .filter(tenant=tenant)
        .order_by("user__username")
    )

    return render(
        request,
        "documents/settings_members.html",
        {
            "tenant": tenant,
            "memberships": memberships,
            "active_settings_section": "members",
        },
    )


def tenant_settings_member_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_members(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = TenantMembershipCreateForm(request.POST, tenant=tenant)
        if form.is_valid():
            AddTenantMember(
                tenant=tenant,
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                roles=list(form.cleaned_data["roles"]),
                actor=request.user,
            ).execute()
            messages.success(request, "Benutzer wurde hinzugefügt.")
            return redirect("documents:settings_members", tenant_slug=tenant.slug)
    else:
        form = TenantMembershipCreateForm(tenant=tenant)

    return render(
        request,
        "documents/settings_member_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Benutzer hinzufügen",
            "submit_label": "Benutzer hinzufügen",
            "active_settings_section": "members",
        },
    )


def tenant_settings_member_edit(
    request: HttpRequest,
    tenant_slug: str,
    membership_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_members(request.user, tenant):
        raise PermissionDenied

    membership = get_object_or_404(
        TenantMembership.objects.select_related("tenant", "user", "role")
        .prefetch_related("roles"),
        id=membership_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = TenantMembershipUpdateForm(request.POST, tenant=tenant)
        if form.is_valid():
            UpdateTenantMembership(
                membership=membership,
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                roles=list(form.cleaned_data["roles"]),
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Benutzer wurde aktualisiert.")
            return redirect("documents:settings_members", tenant_slug=tenant.slug)
    else:
        form = TenantMembershipUpdateForm(
            tenant=tenant,
            initial={
                "email": membership.user.email,
                "roles": membership.roles.all(),
                "is_active": membership.is_active,
            },
        )

    return render(
        request,
        "documents/settings_member_form.html",
        {
            "tenant": tenant,
            "membership": membership,
            "form": form,
            "form_title": "Benutzer bearbeiten",
            "submit_label": "Benutzer speichern",
            "active_settings_section": "members",
        },
    )


def tenant_settings_roles(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_roles(request.user, tenant):
        raise PermissionDenied

    roles = (
        TenantRole.objects.prefetch_related("permissions", "document_spaces")
        .filter(tenant=tenant)
        .order_by("name")
    )
    return render(
        request,
        "documents/settings_roles.html",
        {
            "tenant": tenant,
            "roles": roles,
            "active_settings_section": "roles",
        },
    )


def tenant_settings_role_create(
    request: HttpRequest,
    tenant_slug: str,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_roles(request.user, tenant):
        raise PermissionDenied

    if request.method == "POST":
        form = TenantRoleCreateForm(request.POST, tenant=tenant)
        if form.is_valid():
            CreateTenantRole(
                tenant=tenant,
                name=form.cleaned_data["name"],
                slug=form.cleaned_data["slug"],
                description=form.cleaned_data["description"],
                permissions=list(form.cleaned_data["permissions"]),
                document_spaces=list(form.cleaned_data["document_spaces"]),
                can_access_all_document_spaces=form.cleaned_data[
                    "can_access_all_document_spaces"
                ],
                actor=request.user,
            ).execute()
            messages.success(request, "Rolle wurde erstellt.")
            return redirect("documents:settings_roles", tenant_slug=tenant.slug)
    else:
        form = TenantRoleCreateForm(tenant=tenant)

    return render(
        request,
        "documents/settings_role_form.html",
        {
            "tenant": tenant,
            "form": form,
            "form_title": "Rolle erstellen",
            "submit_label": "Rolle erstellen",
            "active_settings_section": "roles",
        },
    )


def tenant_settings_role_edit(
    request: HttpRequest,
    tenant_slug: str,
    role_id: int,
) -> HttpResponse:
    if not request.user.is_authenticated:
        return _tenant_login_redirect(request, tenant_slug)

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None or not can_manage_roles(request.user, tenant):
        raise PermissionDenied

    role = get_object_or_404(
        TenantRole.objects.prefetch_related("permissions", "document_spaces"),
        id=role_id,
        tenant=tenant,
    )
    if request.method == "POST":
        form = TenantRoleUpdateForm(request.POST, tenant=tenant)
        if form.is_valid():
            UpdateTenantRole(
                role=role,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                permissions=list(form.cleaned_data["permissions"]),
                document_spaces=list(form.cleaned_data["document_spaces"]),
                can_access_all_document_spaces=form.cleaned_data[
                    "can_access_all_document_spaces"
                ],
                is_active=form.cleaned_data["is_active"],
                actor=request.user,
            ).execute()
            messages.success(request, "Rolle wurde aktualisiert.")
            return redirect("documents:settings_roles", tenant_slug=tenant.slug)
    else:
        form = TenantRoleUpdateForm(
            initial={
                "name": role.name,
                "description": role.description,
                "permissions": role.permissions.all(),
                "can_access_all_document_spaces": (
                    role.can_access_all_document_spaces
                ),
                "document_spaces": role.document_spaces.all(),
                "is_active": role.is_active,
            },
            tenant=tenant,
        )

    return render(
        request,
        "documents/settings_role_form.html",
        {
            "tenant": tenant,
            "role": role,
            "form": form,
            "form_title": "Rolle bearbeiten",
            "submit_label": "Rolle speichern",
            "active_settings_section": "roles",
        },
    )
