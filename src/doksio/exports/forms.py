from __future__ import annotations

from django import forms

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.models import Document, DocumentSpace
from doksio.documents.policies import (
    filter_document_spaces_for_user,
    filter_documents_for_user,
)
from doksio.exports.models import ExportRun, ExportRunItem
from doksio.tenancy.models import Tenant


class DocumentImageExportForm(forms.Form):
    document_space = forms.ModelChoiceField(
        label="Dokumentenbox",
        queryset=DocumentSpace.objects.none(),
        required=False,
        empty_label="Alle zugänglichen Dokumentenboxen",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    include_children = forms.BooleanField(
        label="Kindboxen einbeziehen",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    document_date_from = forms.DateField(
        label="Belegdatum von",
        required=False,
        input_formats=["%d.%m.%Y", "%Y-%m-%d"],
        widget=forms.DateInput(
            attrs={
                "class": "form-control",
                "type": "text",
                "inputmode": "numeric",
                "placeholder": "TT.MM.JJJJ",
                "data-smart-date": "true",
            },
            format="%d.%m.%Y",
        ),
    )
    document_date_to = forms.DateField(
        label="Belegdatum bis",
        required=False,
        input_formats=["%d.%m.%Y", "%Y-%m-%d"],
        widget=forms.DateInput(
            attrs={
                "class": "form-control",
                "type": "text",
                "inputmode": "numeric",
                "placeholder": "TT.MM.JJJJ",
                "data-smart-date": "true",
            },
            format="%d.%m.%Y",
        ),
    )

    def __init__(self, *args, tenant: Tenant, user, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.user = user
        self.fields["document_space"].queryset = filter_document_spaces_for_user(
            DocumentSpace.objects.filter(
                tenant=tenant,
                is_active=True,
                datev_document_image_export_enabled=True,
            ),
            user,
            tenant,
            TenantPermissions.DOCUMENTS_EXPORT,
        )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        date_from = cleaned_data.get("document_date_from")
        date_to = cleaned_data.get("document_date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error(
                "document_date_to",
                "Das Bis-Datum darf nicht vor dem Von-Datum liegen.",
            )
        return cleaned_data

    def documents_queryset(self):
        documents = Document.objects.filter(
            tenant=self.tenant,
            status=Document.Status.ACTIVE,
            space__datev_document_image_export_enabled=True,
        )
        documents = filter_documents_for_user(
            documents,
            self.user,
            self.tenant,
            TenantPermissions.DOCUMENTS_EXPORT,
        )
        exported_document_ids = ExportRunItem.objects.filter(
            tenant=self.tenant,
            status=ExportRunItem.Status.EXPORTED,
            export_run__export_type=ExportRun.ExportType.DATEV_DOCUMENT_IMAGES,
        ).values("document_id")
        documents = documents.exclude(id__in=exported_document_ids)

        document_space = self.cleaned_data.get("document_space")
        if document_space is not None:
            if self.cleaned_data.get("include_children"):
                documents = documents.filter(
                    space__path__startswith=f"{document_space.path.rstrip('/')}/",
                ) | documents.filter(space=document_space)
            else:
                documents = documents.filter(space=document_space)

        date_from = self.cleaned_data.get("document_date_from")
        if date_from:
            documents = documents.filter(document_date__gte=date_from)
        date_to = self.cleaned_data.get("document_date_to")
        if date_to:
            documents = documents.filter(document_date__lte=date_to)

        return documents.distinct().order_by("document_date", "created_at", "id")

    def filters_payload(self) -> dict:
        document_space = self.cleaned_data.get("document_space")
        return {
            "document_space_id": document_space.id if document_space else None,
            "document_space_path": document_space.path if document_space else "",
            "include_children": bool(self.cleaned_data.get("include_children")),
            "document_date_from": (
                self.cleaned_data["document_date_from"].isoformat()
                if self.cleaned_data.get("document_date_from")
                else ""
            ),
            "document_date_to": (
                self.cleaned_data["document_date_to"].isoformat()
                if self.cleaned_data.get("document_date_to")
                else ""
            ),
        }
