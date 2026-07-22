from __future__ import annotations

from django import forms

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.metadata import effective_metadata_fields
from doksio.documents.models import DocumentMetadataField, DocumentSpace, DocumentTag
from doksio.documents.policies import filter_document_spaces_for_user
from doksio.tenancy.models import Tenant


class DocumentSearchForm(forms.Form):
    WORKFLOW_STATUS_CHOICES = [
        ("", "Alle"),
        ("open", "Offene Workflows"),
        ("completed", "Alle Workflows erledigt"),
        ("none", "Ohne Workflow"),
    ]
    DOCUMENT_STATUS_CHOICES = [
        ("active", "Aktive Dokumente"),
        ("deleted", "Gelöschte Dokumente"),
        ("all", "Aktive und gelöschte Dokumente"),
    ]
    SORT_CHOICES = [
        ("relevance", "Relevanz"),
        ("created_desc", "Neueste zuerst"),
        ("created_asc", "Älteste zuerst"),
        ("date_desc", "Belegdatum absteigend"),
        ("date_asc", "Belegdatum aufsteigend"),
        ("title_asc", "Titel A-Z"),
    ]

    def __init__(self, *args, tenant: Tenant, user=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.user = user
        self.metadata_filter_fields = []
        box_queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("path")
        if user is not None:
            box_queryset = filter_document_spaces_for_user(
                box_queryset,
                user,
                tenant,
                TenantPermissions.DOCUMENTS_VIEW,
            )
        self.fields["box"].queryset = box_queryset
        self.fields["tags"].queryset = DocumentTag.objects.filter(
            tenant=tenant,
        ).order_by("name")
        selected_box = self._selected_box()
        if selected_box is not None:
            self.metadata_filter_fields = effective_metadata_fields(selected_box)
            for field_definition in self.metadata_filter_fields:
                self._add_metadata_filter_field(field_definition)

    def _selected_box(self) -> DocumentSpace | None:
        raw_box = None
        if self.is_bound:
            raw_box = self.data.get(self.add_prefix("box"))
        else:
            raw_box = self.initial.get("box")
        if not raw_box:
            return None
        try:
            return self.fields["box"].queryset.get(id=raw_box)
        except (DocumentSpace.DoesNotExist, ValueError, TypeError):
            return None

    def _add_metadata_filter_field(
        self,
        field_definition: DocumentMetadataField,
    ) -> None:
        base_name = f"metadata_{field_definition.slug}"
        if field_definition.field_type in {
            DocumentMetadataField.FieldType.TEXT,
            DocumentMetadataField.FieldType.MULTILINE_TEXT,
        }:
            self.fields[base_name] = forms.CharField(
                label=field_definition.name,
                required=False,
                widget=forms.TextInput(attrs={"class": "form-control"}),
            )
        elif field_definition.field_type == DocumentMetadataField.FieldType.CHOICE:
            choices = [("", "Alle")]
            choices.extend((choice, choice) for choice in field_definition.choices)
            self.fields[base_name] = forms.ChoiceField(
                label=field_definition.name,
                required=False,
                choices=choices,
                widget=forms.Select(attrs={"class": "form-select"}),
            )
        elif field_definition.field_type == DocumentMetadataField.FieldType.BOOLEAN:
            self.fields[base_name] = forms.ChoiceField(
                label=field_definition.name,
                required=False,
                choices=[("", "Alle"), ("true", "Ja"), ("false", "Nein")],
                widget=forms.Select(attrs={"class": "form-select"}),
            )
        elif field_definition.field_type == DocumentMetadataField.FieldType.DATE:
            self.fields[f"{base_name}_from"] = forms.DateField(
                label=f"{field_definition.name} von",
                required=False,
                input_formats=["%d.%m.%Y", "%Y-%m-%d"],
                widget=forms.DateInput(
                    attrs={
                        "class": "form-control",
                        "type": "text",
                        "inputmode": "numeric",
                        "placeholder": "TT.MM.JJJJ, today, +1week",
                        "data-smart-date": "true",
                    },
                    format="%d.%m.%Y",
                ),
            )
            self.fields[f"{base_name}_to"] = forms.DateField(
                label=f"{field_definition.name} bis",
                required=False,
                input_formats=["%d.%m.%Y", "%Y-%m-%d"],
                widget=forms.DateInput(
                    attrs={
                        "class": "form-control",
                        "type": "text",
                        "inputmode": "numeric",
                        "placeholder": "TT.MM.JJJJ, today, +1week",
                        "data-smart-date": "true",
                    },
                    format="%d.%m.%Y",
                ),
            )
        elif field_definition.field_type == DocumentMetadataField.FieldType.NUMBER:
            self.fields[f"{base_name}_min"] = forms.DecimalField(
                label=f"{field_definition.name} min.",
                required=False,
                widget=forms.NumberInput(attrs={"class": "form-control"}),
            )
            self.fields[f"{base_name}_max"] = forms.DecimalField(
                label=f"{field_definition.name} max.",
                required=False,
                widget=forms.NumberInput(attrs={"class": "form-control"}),
            )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        metadata_filters = []
        for field_definition in self.metadata_filter_fields:
            base_name = f"metadata_{field_definition.slug}"
            if field_definition.field_type in {
                DocumentMetadataField.FieldType.TEXT,
                DocumentMetadataField.FieldType.MULTILINE_TEXT,
            }:
                value = cleaned_data.get(base_name)
                if value not in (None, ""):
                    metadata_filters.append(
                        {
                            "field": field_definition,
                            "operator": "contains",
                            "value": value,
                        }
                    )
            elif field_definition.field_type in {
                DocumentMetadataField.FieldType.CHOICE,
                DocumentMetadataField.FieldType.BOOLEAN,
            }:
                value = cleaned_data.get(base_name)
                if value not in (None, ""):
                    metadata_filters.append(
                        {
                            "field": field_definition,
                            "operator": "exact",
                            "value": value,
                        }
                    )
            elif field_definition.field_type == DocumentMetadataField.FieldType.DATE:
                from_value = cleaned_data.get(f"{base_name}_from")
                to_value = cleaned_data.get(f"{base_name}_to")
                if from_value:
                    metadata_filters.append(
                        {
                            "field": field_definition,
                            "operator": "gte",
                            "value": from_value.isoformat(),
                        }
                    )
                if to_value:
                    metadata_filters.append(
                        {
                            "field": field_definition,
                            "operator": "lte",
                            "value": to_value.isoformat(),
                        }
                    )
            elif field_definition.field_type == DocumentMetadataField.FieldType.NUMBER:
                min_value = cleaned_data.get(f"{base_name}_min")
                max_value = cleaned_data.get(f"{base_name}_max")
                if min_value is not None:
                    metadata_filters.append(
                        {
                            "field": field_definition,
                            "operator": "gte",
                            "value": min_value,
                        }
                    )
                if max_value is not None:
                    metadata_filters.append(
                        {
                            "field": field_definition,
                            "operator": "lte",
                            "value": max_value,
                        }
                    )
        cleaned_data["metadata_filters"] = metadata_filters
        return cleaned_data

    q = forms.CharField(
        label="Suchbegriff",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Titel, OCR-Text, Dateiname, Tag, Kommentar",
            }
        ),
    )
    tags = forms.ModelMultipleChoiceField(
        label="Tags",
        required=False,
        queryset=DocumentTag.objects.none(),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 5}),
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
                "placeholder": "TT.MM.JJJJ, today, +1week",
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
                "placeholder": "TT.MM.JJJJ, today, +1week",
                "data-smart-date": "true",
            },
            format="%d.%m.%Y",
        ),
    )
    box = forms.ModelChoiceField(
        label="Dokumentenbox",
        required=False,
        queryset=DocumentSpace.objects.none(),
        empty_label="Alle Boxen",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    include_child_boxes = forms.BooleanField(
        label="Unterboxen einschließen",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    workflow_status = forms.ChoiceField(
        label="Workflow-Status",
        required=False,
        choices=WORKFLOW_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    document_status = forms.ChoiceField(
        label="Dokumentstatus",
        required=False,
        choices=DOCUMENT_STATUS_CHOICES,
        initial="active",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort = forms.ChoiceField(
        label="Sortierung",
        required=False,
        choices=SORT_CHOICES,
        initial="relevance",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
