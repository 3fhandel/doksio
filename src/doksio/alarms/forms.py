from __future__ import annotations

from django import forms

from doksio.accounts.permissions import TenantPermissions
from doksio.alarms.models import DocumentAlarm
from doksio.documents.models import DocumentSpace, DocumentTag
from doksio.documents.policies import filter_document_spaces_for_user


class DocumentAlarmForm(forms.ModelForm):
    class Meta:
        model = DocumentAlarm
        fields = [
            "name",
            "search_term",
            "document_space",
            "include_child_spaces",
            "tags",
            "notify_in_app",
            "notify_email",
            "is_active",
        ]
        labels = {
            "name": "Name",
            "search_term": "Suchbegriff",
            "document_space": "Dokumentenbox",
            "include_child_spaces": "Kindboxen einbeziehen",
            "tags": "Tags",
            "notify_in_app": "In-App-Benachrichtigung",
            "notify_email": "E-Mail-Benachrichtigung",
            "is_active": "Alarm aktiv",
        }
        help_texts = {
            "search_term": (
                "Durchsucht Titel, Dateinamen, Tags, Metadaten und OCR-Volltext."
            ),
            "tags": "Ein Dokument muss alle ausgewählten Tags besitzen.",
        }
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "search_term": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "z. B. XYZ",
                }
            ),
            "document_space": forms.Select(attrs={"class": "form-select"}),
            "include_child_spaces": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "tags": forms.SelectMultiple(
                attrs={"class": "form-select", "size": 6}
            ),
            "notify_in_app": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "notify_email": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, tenant, user, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.user = user
        spaces = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
            deleted_at__isnull=True,
        ).order_by("path")
        self.fields["document_space"].queryset = filter_document_spaces_for_user(
            spaces,
            user,
            tenant,
            TenantPermissions.DOCUMENTS_VIEW,
        )
        self.fields["document_space"].empty_label = "Alle zugänglichen Boxen"
        self.fields["tags"].queryset = DocumentTag.objects.filter(
            tenant=tenant,
        ).order_by("name")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicate = DocumentAlarm.objects.filter(
            tenant=self.tenant,
            owner=self.user,
            name=name,
        ).exclude(id=self.instance.id)
        if duplicate.exists():
            raise forms.ValidationError("Ein Alarm mit diesem Namen existiert bereits.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("notify_in_app") and not cleaned_data.get(
            "notify_email"
        ):
            raise forms.ValidationError(
                "Mindestens ein Benachrichtigungskanal muss aktiv sein."
            )
        return cleaned_data
