from __future__ import annotations

from django import forms

from domasy.documents.models import DocumentSpace, DocumentTag
from domasy.tenancy.models import Tenant


class DocumentSearchForm(forms.Form):
    OCR_STATUS_CHOICES = [
        ("", "Alle"),
        ("succeeded", "OCR erfolgreich"),
        ("pending", "OCR wartet"),
        ("running", "OCR läuft"),
        ("failed", "OCR fehlgeschlagen"),
        ("none", "Ohne OCR"),
    ]
    SORT_CHOICES = [
        ("relevance", "Relevanz"),
        ("created_desc", "Neueste zuerst"),
        ("created_asc", "Älteste zuerst"),
        ("date_desc", "Belegdatum absteigend"),
        ("date_asc", "Belegdatum aufsteigend"),
        ("title_asc", "Titel A-Z"),
    ]

    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["box"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("path")
        self.fields["tags"].queryset = DocumentTag.objects.filter(
            tenant=tenant,
        ).order_by("name")

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
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
    )
    document_date_to = forms.DateField(
        label="Belegdatum bis",
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
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
    ocr_status = forms.ChoiceField(
        label="OCR-Status",
        required=False,
        choices=OCR_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort = forms.ChoiceField(
        label="Sortierung",
        required=False,
        choices=SORT_CHOICES,
        initial="relevance",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
