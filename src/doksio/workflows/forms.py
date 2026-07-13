from __future__ import annotations

from django import forms
from django.utils.text import slugify

from doksio.accounts.models import TenantRole
from doksio.documents.models import DocumentSpace
from doksio.tenancy.models import Tenant
from doksio.workflows.models import WorkflowStep, WorkflowTemplate


class WorkflowTemplateForm(forms.Form):
    name = forms.CharField(
        label="Name",
        max_length=160,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    slug = forms.SlugField(
        label="Slug",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    description = forms.CharField(
        label="Beschreibung",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    trigger_type = forms.ChoiceField(
        label="Start",
        choices=WorkflowTemplate.TriggerType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    trigger_document_space = forms.ModelChoiceField(
        label="Trigger-Dokumentenbox",
        required=False,
        queryset=DocumentSpace.objects.none(),
        empty_label="Alle Dokumentenboxen",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Nur relevant für automatische Starts bei neu importierten Dokumenten."
        ),
    )
    trigger_include_child_spaces = forms.BooleanField(
        label="Unterboxen einschließen",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    is_active = forms.BooleanField(
        label="Aktiv",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(
        self,
        *args,
        tenant: Tenant,
        template: WorkflowTemplate | None = None,
        **kwargs,
    ) -> None:
        self.tenant = tenant
        self.template = template
        super().__init__(*args, **kwargs)
        self.fields["trigger_document_space"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("path")

    def clean_slug(self) -> str:
        name = self.cleaned_data.get("name", "")
        slug = self.cleaned_data["slug"] or slugify(name)
        if not slug:
            raise forms.ValidationError("Der Slug darf nicht leer sein.")

        duplicates = WorkflowTemplate.objects.filter(
            tenant=self.tenant,
            slug=slug,
        )
        if self.template is not None:
            duplicates = duplicates.exclude(id=self.template.id)
        if duplicates.exists():
            raise forms.ValidationError("Dieser Workflow-Slug existiert bereits.")
        return slug


class WorkflowStepForm(forms.Form):
    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["assigned_role"].queryset = TenantRole.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("name")

    name = forms.CharField(
        label="Name",
        max_length=160,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    step_type = forms.ChoiceField(
        label="Typ",
        choices=WorkflowStep.StepType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    assigned_role = forms.ModelChoiceField(
        label="Zuständige Rolle",
        required=False,
        queryset=TenantRole.objects.none(),
        empty_label="Keine feste Rolle",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    instructions = forms.CharField(
        label="Anweisung",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    sort_order = forms.IntegerField(
        label="Reihenfolge",
        min_value=0,
        initial=100,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    comment_policy = forms.ChoiceField(
        label="Kommentar",
        choices=WorkflowStep.CommentPolicy.choices,
        initial=WorkflowStep.CommentPolicy.OPTIONAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class StartWorkflowForm(forms.Form):
    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = WorkflowTemplate.objects.filter(
            tenant=tenant,
            is_active=True,
            trigger_type=WorkflowTemplate.TriggerType.MANUAL,
        ).order_by("name")

    template = forms.ModelChoiceField(
        label="Workflow",
        queryset=WorkflowTemplate.objects.none(),
        empty_label="Bitte wählen",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class CompleteWorkflowTaskForm(forms.Form):
    task_id = forms.IntegerField(widget=forms.HiddenInput)
    comment = forms.CharField(
        label="Kommentar",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
