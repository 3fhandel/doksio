from __future__ import annotations

import json
import re

from django import forms
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.utils.text import slugify

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.metadata import metadata_field_slug_is_available
from doksio.documents.models import (
    Document,
    DocumentImportBatchItem,
    DocumentMetadataField,
    DocumentSpace,
    DocumentTitleRule,
)
from doksio.documents.policies import filter_document_spaces_for_user
from doksio.documents.title_rules import DEFAULT_EINVOICE_TITLE_FORMAT
from doksio.tenancy.models import Tenant


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(item, initial) for item in data]
        return [single_file_clean(data, initial)]


class DocumentUploadForm(forms.Form):
    def __init__(
        self,
        *args,
        tenant: Tenant,
        user: AbstractBaseUser | AnonymousUser | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        spaces = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        )
        if user is not None:
            spaces = filter_document_spaces_for_user(
                spaces,
                user,
                tenant,
                TenantPermissions.DOCUMENTS_UPLOAD,
            )
        self.fields["space"].queryset = spaces.order_by("path")

    title = forms.CharField(
        label="Titel",
        max_length=255,
        required=False,
        help_text=(
            "Optional beim Einzelupload. Bei mehreren Dateien setzt Doksio "
            "den Titel je Dokument automatisch."
        ),
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    space = forms.ModelChoiceField(
        label="Dokumentenbox",
        queryset=DocumentSpace.objects.none(),
        required=False,
        empty_label="Bitte wählen",
        help_text=("Optional, wenn für Uploads eine Importstrategie hinterlegt ist."),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    file = MultipleFileField(
        label="Dateien",
        widget=MultipleFileInput(
            attrs={"class": "form-control", "multiple": True},
        ),
    )


class DocumentTitleRuleForm(forms.ModelForm):
    class Meta:
        model = DocumentTitleRule
        fields = [
            "document_space",
            "strategy",
            "regex_search",
            "regex_replace",
            "einvoice_format",
            "fallback_strategy",
        ]
        labels = {
            "document_space": "Geltungsbereich",
            "strategy": "Strategie",
            "regex_search": "RegEx-Suche",
            "regex_replace": "Ersetzung",
            "einvoice_format": "Format-String",
            "fallback_strategy": "Fallback",
        }
        help_texts = {
            "regex_search": (
                "Regulärer Ausdruck auf dem OCR-Volltext. Gruppen können in "
                "der Ersetzung verwendet werden, z. B. \\1."
            ),
            "regex_replace": "Zum Beispiel Rechnung \\1 oder \\g<nummer>.",
            "einvoice_format": (
                "Platzhalter werden in geschweiften Klammern angegeben. "
                "Mit :.12 kann ein Wert auf zwölf Zeichen gekürzt werden."
            ),
        }
        widgets = {
            "document_space": forms.Select(attrs={"class": "form-select"}),
            "strategy": forms.Select(attrs={"class": "form-select"}),
            "regex_search": forms.TextInput(attrs={"class": "form-control"}),
            "regex_replace": forms.TextInput(attrs={"class": "form-control"}),
            "einvoice_format": forms.TextInput(attrs={"class": "form-control"}),
            "fallback_strategy": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(
        self,
        *args,
        tenant: Tenant,
        lock_scope: bool = False,
        **kwargs,
    ) -> None:
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        used_rules = DocumentTitleRule.objects.filter(
            tenant=tenant,
            document_space__isnull=False,
        )
        if self.instance.pk:
            used_rules = used_rules.exclude(pk=self.instance.pk)
        self.fields["document_space"].queryset = (
            DocumentSpace.objects.filter(
                tenant=tenant,
                is_active=True,
                deleted_at__isnull=True,
            )
            .exclude(id__in=used_rules.values("document_space_id"))
            .order_by("path")
        )
        self.fields["document_space"].required = False
        self.fields["document_space"].empty_label = "Tenant-Standard"
        self.fields["fallback_strategy"].required = False
        if lock_scope:
            self.fields["document_space"].disabled = True

    def clean_document_space(self) -> DocumentSpace | None:
        document_space = self.cleaned_data.get("document_space")
        if document_space is not None and document_space.tenant_id != self.tenant.id:
            raise forms.ValidationError(
                "Die Dokumentenbox gehört nicht zu diesem Tenant."
            )
        if (
            document_space is None
            and DocumentTitleRule.objects.filter(
                tenant=self.tenant,
                document_space__isnull=True,
            )
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError(
                "Für diesen Tenant ist bereits eine Standardregel vorhanden."
            )
        return document_space

    def clean(self) -> dict:
        cleaned_data = super().clean()
        strategy = cleaned_data.get("strategy")
        if not cleaned_data.get("fallback_strategy"):
            cleaned_data["fallback_strategy"] = (
                DocumentTitleRule.FallbackStrategy.AUTOMATIC
            )
        uses_regex = strategy == DocumentTitleRule.Strategy.REGEX or (
            strategy == DocumentTitleRule.Strategy.EINVOICE
            and cleaned_data.get("fallback_strategy")
            == DocumentTitleRule.FallbackStrategy.REGEX
        )
        if uses_regex:
            pattern = cleaned_data.get("regex_search", "").strip()
            if not pattern:
                self.add_error(
                    "regex_search",
                    "Für die RegEx-Strategie ist ein Suchmuster erforderlich.",
                )
            else:
                try:
                    re.compile(pattern)
                except re.error as error:
                    self.add_error(
                        "regex_search",
                        f"Ungültiger regulärer Ausdruck: {error}",
                    )
        return cleaned_data

    def save(self, commit: bool = True) -> DocumentTitleRule:
        rule = super().save(commit=False)
        rule.tenant = self.tenant
        uses_regex = rule.strategy == DocumentTitleRule.Strategy.REGEX or (
            rule.strategy == DocumentTitleRule.Strategy.EINVOICE
            and rule.fallback_strategy == DocumentTitleRule.FallbackStrategy.REGEX
        )
        if not uses_regex:
            rule.regex_search = ""
            rule.regex_replace = ""
        if rule.strategy != DocumentTitleRule.Strategy.EINVOICE:
            rule.einvoice_format = DEFAULT_EINVOICE_TITLE_FORMAT
            rule.fallback_strategy = DocumentTitleRule.FallbackStrategy.AUTOMATIC
        if commit:
            rule.save()
        return rule


class DocumentImportBatchUploadForm(forms.Form):
    title = forms.CharField(
        label="Name des Stapels",
        max_length=255,
        required=False,
        help_text="Optional. Ohne Eingabe vergibt Doksio einen Namen automatisch.",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    file = MultipleFileField(
        label="Dateien",
        widget=MultipleFileInput(
            attrs={"class": "form-control", "multiple": True},
        ),
    )


class DocumentImportBatchItemForm(forms.Form):
    def __init__(
        self,
        *args,
        item: DocumentImportBatchItem,
        tenant: Tenant,
        user: AbstractBaseUser | AnonymousUser,
        **kwargs,
    ) -> None:
        self.item = item
        super().__init__(*args, **kwargs)
        spaces = filter_document_spaces_for_user(
            DocumentSpace.objects.filter(
                tenant=tenant,
                is_active=True,
                deleted_at__isnull=True,
            ),
            user,
            tenant,
            TenantPermissions.DOCUMENTS_UPLOAD,
        ).order_by("path")
        self.fields["target_space"].queryset = spaces

    target_space = forms.ModelChoiceField(
        label="Dokumentenbox",
        queryset=DocumentSpace.objects.none(),
        required=False,
        empty_label="Bitte wählen",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    skip = forms.BooleanField(
        label="Überspringen",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class DocumentBoxScanOptimizationForm(forms.Form):
    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["space"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
            deleted_at__isnull=True,
        ).order_by("path")

    space = forms.ModelChoiceField(
        label="Dokumentenbox",
        queryset=DocumentSpace.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    include_children = forms.BooleanField(
        label="Kindboxen einschließen",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class DocumentBoxTitleRefreshForm(forms.Form):
    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["space"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
            deleted_at__isnull=True,
        ).order_by("path")

    space = forms.ModelChoiceField(
        label="Dokumentenbox",
        queryset=DocumentSpace.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    include_children = forms.BooleanField(
        label="Kindboxen einschließen",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class DocumentCoreMetadataForm(forms.Form):
    def __init__(
        self,
        *args,
        tenant: Tenant,
        user: AbstractBaseUser | AnonymousUser | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        spaces = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        )
        if user is not None:
            spaces = filter_document_spaces_for_user(
                spaces,
                user,
                tenant,
                TenantPermissions.DOCUMENTS_UPLOAD,
            )
        self.fields["space"].queryset = spaces.order_by("path")

    title = forms.CharField(
        label="Dokumenttitel",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    document_date = forms.DateField(
        label="Belegdatum",
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
    space = forms.ModelChoiceField(
        label="Dokumentenbox",
        queryset=DocumentSpace.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )


DOCUMENT_DELETE_REASON_CHOICES = [
    ("", "Bitte wählen"),
    ("Fehlimport", "Fehlimport"),
    ("Dublette", "Dublette"),
    ("Testupload", "Testupload"),
    ("Falsche Dokumentenbox", "Falsche Dokumentenbox"),
    ("Falscher Tenant", "Falscher Tenant"),
    ("Datenschutz/Retention", "Datenschutz/Retention"),
    ("Sonstiges", "Sonstiges"),
]


class DocumentDeleteForm(forms.Form):
    reason = forms.ChoiceField(
        label="Löschgrund",
        choices=DOCUMENT_DELETE_REASON_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class DocumentShareAttachmentForm(forms.Form):
    recipient = forms.EmailField(
        label="Empfänger",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    message = forms.CharField(
        label="Nachricht",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )


class DocumentRelationForm(forms.Form):
    def __init__(self, *args, document: Document, user, **kwargs) -> None:
        self.document = document
        self.user = user
        super().__init__(*args, **kwargs)

    target_document_id = forms.IntegerField(
        label="Dokument",
        min_value=1,
        widget=forms.HiddenInput,
    )

    def clean_target_document_id(self):
        target_document_id = self.cleaned_data["target_document_id"]
        if target_document_id == self.document.id:
            raise forms.ValidationError(
                "Ein Dokument kann nicht mit sich selbst verknüpft werden."
            )
        target_document = (
            Document.objects.select_related("tenant", "space")
            .filter(
                id=target_document_id,
                tenant=self.document.tenant,
                status=Document.Status.ACTIVE,
            )
            .first()
        )
        if target_document is None:
            raise forms.ValidationError("Dieses Dokument wurde nicht gefunden.")
        from doksio.documents.policies import can_view_document

        if not can_view_document(self.user, target_document):
            raise forms.ValidationError("Du darfst dieses Dokument nicht sehen.")
        return target_document


class DocumentSplitForm(forms.Form):
    ORIGINAL_HANDLING_CHOICES = [
        ("keep", "Originaldokument behalten"),
        ("delete", "Originaldokument nach erfolgreicher Aufteilung löschen"),
    ]

    def __init__(
        self,
        *args,
        tenant: Tenant,
        user: AbstractBaseUser | AnonymousUser,
        page_count: int,
        **kwargs,
    ) -> None:
        self.tenant = tenant
        self.user = user
        self.page_count = page_count
        super().__init__(*args, **kwargs)
        self.target_spaces = filter_document_spaces_for_user(
            DocumentSpace.objects.filter(
                tenant=tenant,
                is_active=True,
                deleted_at__isnull=True,
            ),
            user,
            tenant,
            TenantPermissions.DOCUMENTS_UPLOAD,
        ).order_by("path")

    split_payload = forms.CharField(widget=forms.HiddenInput)
    original_handling = forms.ChoiceField(
        label="Originaldatei",
        choices=ORIGINAL_HANDLING_CHOICES,
        initial="keep",
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    def clean_split_payload(self):
        raw_payload = self.cleaned_data["split_payload"]
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(
                "Die Aufteilung konnte nicht gelesen werden."
            ) from exc

        if not isinstance(payload, list) or len(payload) < 2:
            raise forms.ValidationError(
                "Mindestens zwei Teildokumente sind erforderlich."
            )

        allowed_spaces = {space.id: space for space in self.target_spaces}
        cleaned_parts = []
        expected_start = 1
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise forms.ValidationError("Ungültiger Abschnitt.")
            try:
                start_page = int(item.get("start_page"))
                end_page = int(item.get("end_page"))
                target_space_id = int(item.get("target_space_id"))
            except (TypeError, ValueError) as exc:
                raise forms.ValidationError(
                    f"Abschnitt {index} ist unvollständig."
                ) from exc

            if start_page != expected_start or end_page < start_page:
                raise forms.ValidationError("Die Seitenbereiche müssen lückenlos sein.")
            if end_page > self.page_count:
                raise forms.ValidationError(
                    "Ein Seitenbereich liegt außerhalb des PDFs."
                )
            target_space = allowed_spaces.get(target_space_id)
            if target_space is None:
                raise forms.ValidationError(
                    "Für mindestens eine Zielbox fehlt die Berechtigung."
                )
            cleaned_parts.append(
                {
                    "start_page": start_page,
                    "end_page": end_page,
                    "target_space": target_space,
                    "title": str(item.get("title", "")).strip(),
                }
            )
            expected_start = end_page + 1

        if expected_start != self.page_count + 1:
            raise forms.ValidationError("Die Aufteilung muss alle Seiten abdecken.")
        return cleaned_parts


class DocumentSpaceForm(forms.Form):
    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
            deleted_at__isnull=True,
        ).order_by("path")

    name = forms.CharField(
        label="Name",
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    slug = forms.SlugField(
        label="Slug",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    parent = forms.ModelChoiceField(
        label="Übergeordnete Box",
        queryset=DocumentSpace.objects.none(),
        required=False,
        empty_label="Root",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    description = forms.CharField(
        label="Beschreibung",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    datev_document_image_export_enabled = forms.BooleanField(
        label="DATEV-Belegbild-Export aktiv",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        slug = cleaned_data.get("slug") or slugify(name or "")
        parent = cleaned_data.get("parent")
        if not slug:
            raise forms.ValidationError("Der Slug darf nicht leer sein.")

        path = f"/{slug}" if parent is None else f"{parent.path.rstrip('/')}/{slug}"
        if DocumentSpace.objects.filter(tenant=self.tenant, path=path).exists():
            raise forms.ValidationError("Diese Dokumentenbox existiert bereits.")
        cleaned_data["slug"] = slug
        return cleaned_data


class DocumentSpaceUpdateForm(DocumentSpaceForm):
    is_active = forms.BooleanField(
        label="Aktiv",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(
        self,
        *args,
        tenant: Tenant,
        document_space: DocumentSpace,
        **kwargs,
    ) -> None:
        self.document_space = document_space
        super().__init__(*args, tenant=tenant, **kwargs)
        self.fields["parent"].queryset = (
            self.fields["parent"]
            .queryset.exclude(
                path__startswith=f"{document_space.path.rstrip('/')}/",
            )
            .exclude(id=document_space.id)
        )

    def clean(self) -> dict:
        cleaned_data = super(DocumentSpaceForm, self).clean()
        name = cleaned_data.get("name")
        slug = cleaned_data.get("slug") or slugify(name or "")
        parent = cleaned_data.get("parent")
        if not slug:
            raise forms.ValidationError("Der Slug darf nicht leer sein.")

        path = f"/{slug}" if parent is None else f"{parent.path.rstrip('/')}/{slug}"
        duplicate_exists = (
            DocumentSpace.objects.filter(tenant=self.tenant, path=path)
            .exclude(id=self.document_space.id)
            .exists()
        )
        if duplicate_exists:
            raise forms.ValidationError("Diese Dokumentenbox existiert bereits.")
        cleaned_data["slug"] = slug
        return cleaned_data


class DocumentSpaceDeleteForm(forms.Form):
    class Strategy:
        MOVE = "move"
        DELETE_DOCUMENTS = "delete_documents"

    strategy = forms.ChoiceField(
        label="Umgang mit Dokumenten",
        choices=[
            (Strategy.MOVE, "Dokumente in eine andere Box verschieben"),
            (Strategy.DELETE_DOCUMENTS, "Dokumente mitlöschen"),
        ],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    target_space = forms.ModelChoiceField(
        label="Zielbox",
        queryset=DocumentSpace.objects.none(),
        required=False,
        empty_label="Bitte wählen",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    delete_reason = forms.ChoiceField(
        label="Löschgrund für Dokumente",
        choices=DOCUMENT_DELETE_REASON_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(
        self,
        *args,
        tenant: Tenant,
        document_space: DocumentSpace,
        **kwargs,
    ) -> None:
        self.document_space = document_space
        super().__init__(*args, **kwargs)
        self.fields["target_space"].queryset = (
            DocumentSpace.objects.filter(
                tenant=tenant,
                is_active=True,
                deleted_at__isnull=True,
            )
            .exclude(id=document_space.id)
            .exclude(path__startswith=f"{document_space.path.rstrip('/')}/")
            .order_by("path")
        )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        strategy = cleaned_data.get("strategy")
        target_space = cleaned_data.get("target_space")
        delete_reason = cleaned_data.get("delete_reason")
        if strategy == self.Strategy.MOVE and target_space is None:
            self.add_error("target_space", "Bitte eine Zielbox wählen.")
        if strategy == self.Strategy.DELETE_DOCUMENTS and not delete_reason:
            self.add_error("delete_reason", "Bitte einen Löschgrund wählen.")
        return cleaned_data


class DocumentSpaceEmptyForm(forms.Form):
    confirm_name = forms.CharField(
        label="Name der Dokumentenbox",
        help_text="Zur Bestätigung exakt den Namen der Dokumentenbox eingeben.",
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )

    def __init__(
        self,
        *args,
        document_space: DocumentSpace,
        **kwargs,
    ) -> None:
        self.document_space = document_space
        super().__init__(*args, **kwargs)

    def clean_confirm_name(self) -> str:
        confirm_name = self.cleaned_data["confirm_name"].strip()
        if confirm_name != self.document_space.name:
            raise forms.ValidationError(
                "Der eingegebene Name stimmt nicht mit der Dokumentenbox überein."
            )
        return confirm_name


class DocumentMetadataFieldForm(forms.Form):
    name = forms.CharField(
        label="Name",
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    slug = forms.SlugField(
        label="Slug",
        max_length=80,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    field_type = forms.ChoiceField(
        label="Feldtyp",
        choices=DocumentMetadataField.FieldType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    help_text = forms.CharField(
        label="Hilfetext",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    choices_text = forms.CharField(
        label="Auswahlwerte",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text="Nur für Auswahlfelder. Ein Wert pro Zeile.",
    )
    allow_custom_choices = forms.BooleanField(
        label="User darf Einträge hinzufügen",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    einvoice_source = forms.ChoiceField(
        label="Aus eRechnung übernehmen",
        choices=DocumentMetadataField.EInvoiceSource.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    sort_order = forms.IntegerField(
        label="Reihenfolge",
        min_value=0,
        initial=100,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    is_required = forms.BooleanField(
        label="Pflichtfeld",
        required=False,
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
        document_space: DocumentSpace,
        metadata_field: DocumentMetadataField | None = None,
        **kwargs,
    ) -> None:
        self.tenant = tenant
        self.document_space = document_space
        self.metadata_field = metadata_field
        super().__init__(*args, **kwargs)

    def clean_slug(self) -> str:
        name = self.cleaned_data.get("name", "")
        slug = self.cleaned_data["slug"] or slugify(name)
        if not slug:
            raise forms.ValidationError("Der Slug darf nicht leer sein.")

        duplicates = DocumentMetadataField.objects.filter(
            space=self.document_space,
            slug=slug,
        )
        if self.metadata_field is not None:
            duplicates = duplicates.exclude(id=self.metadata_field.id)
        if duplicates.exists():
            raise forms.ValidationError("Dieser Metadaten-Slug existiert bereits.")
        if not metadata_field_slug_is_available(
            space=self.document_space,
            slug=slug,
            exclude_field=self.metadata_field,
        ):
            raise forms.ValidationError(
                "Dieser Metadaten-Slug ist bereits in einer Eltern- oder Kindbox "
                "vergeben."
            )
        return slug

    def clean(self) -> dict:
        cleaned_data = super().clean()
        field_type = cleaned_data.get("field_type")
        choices_text = cleaned_data.get("choices_text", "")
        choices = [
            choice.strip() for choice in choices_text.splitlines() if choice.strip()
        ]
        if field_type == DocumentMetadataField.FieldType.CHOICE and not choices:
            self.add_error(
                "choices_text",
                "Auswahlfelder benötigen mindestens einen Auswahlwert.",
            )
        cleaned_data["choices"] = choices
        return cleaned_data


class DocumentMetadataForm(forms.Form):
    def __init__(
        self,
        *args,
        metadata_fields,
        metadata: dict,
        **kwargs,
    ) -> None:
        self.metadata_fields = list(metadata_fields)
        super().__init__(*args, **kwargs)
        for field_definition in self.metadata_fields:
            field_name = f"metadata_{field_definition.slug}"
            initial_value = metadata.get(field_definition.slug)
            self.fields[field_name] = self._build_field(
                field_definition=field_definition,
                initial_value=initial_value,
            )
            self._add_custom_choice_field(field_definition)

    def _build_field(
        self,
        field_definition: DocumentMetadataField,
        initial_value,
    ) -> forms.Field:
        attrs = {"class": "form-control"}
        kwargs = {
            "label": field_definition.name,
            "required": field_definition.is_required,
            "help_text": field_definition.help_text,
            "initial": initial_value,
        }
        if (
            field_definition.field_type
            == DocumentMetadataField.FieldType.MULTILINE_TEXT
        ):
            return forms.CharField(
                **kwargs,
                widget=forms.Textarea(attrs={**attrs, "rows": 3}),
            )
        if field_definition.field_type == DocumentMetadataField.FieldType.DATE:
            return forms.DateField(
                **kwargs,
                input_formats=["%d.%m.%Y", "%Y-%m-%d"],
                widget=forms.DateInput(
                    attrs={
                        **attrs,
                        "type": "text",
                        "inputmode": "numeric",
                        "placeholder": "TT.MM.JJJJ",
                        "data-smart-date": "true",
                    },
                    format="%d.%m.%Y",
                ),
            )
        if field_definition.field_type == DocumentMetadataField.FieldType.NUMBER:
            return forms.DecimalField(**kwargs, widget=forms.NumberInput(attrs=attrs))
        if field_definition.field_type == DocumentMetadataField.FieldType.BOOLEAN:
            return forms.BooleanField(
                label=field_definition.name,
                required=False,
                help_text=field_definition.help_text,
                initial=bool(initial_value),
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )
        if field_definition.field_type == DocumentMetadataField.FieldType.CHOICE:
            choices = [("", "Bitte wählen")]
            choices.extend((choice, choice) for choice in field_definition.choices)
            attrs = {"class": "form-select"}
            if field_definition.allow_custom_choices:
                attrs.update(
                    {
                        "data-metadata-choice-select": "true",
                        "data-metadata-choice-target": (
                            f"id_metadata_{field_definition.slug}_new_choice"
                        ),
                    }
                )
            return forms.ChoiceField(
                **{
                    **kwargs,
                    "required": (
                        field_definition.is_required
                        and not field_definition.allow_custom_choices
                    ),
                },
                choices=choices,
                widget=forms.Select(attrs=attrs),
            )
        return forms.CharField(**kwargs, widget=forms.TextInput(attrs=attrs))

    def _add_custom_choice_field(
        self,
        field_definition: DocumentMetadataField,
    ) -> None:
        if (
            field_definition.field_type == DocumentMetadataField.FieldType.CHOICE
            and field_definition.allow_custom_choices
        ):
            self.fields[f"metadata_{field_definition.slug}_new_choice"] = (
                forms.CharField(
                    label=f"{field_definition.name}: Eintrag hinzufügen",
                    required=False,
                    widget=forms.TextInput(
                        attrs={
                            "class": "form-control metadata-choice-new-input",
                            "placeholder": "Neuer Auswahlwert",
                            "data-metadata-choice-input": "true",
                        }
                    ),
                )
            )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        for field_definition in self.metadata_fields:
            if (
                field_definition.field_type != DocumentMetadataField.FieldType.CHOICE
                or not field_definition.allow_custom_choices
                or not field_definition.is_required
            ):
                continue
            selected_value = cleaned_data.get(f"metadata_{field_definition.slug}")
            new_value = cleaned_data.get(
                f"metadata_{field_definition.slug}_new_choice",
                "",
            ).strip()
            if not selected_value and not new_value:
                self.add_error(
                    f"metadata_{field_definition.slug}",
                    "Bitte Wert wählen oder neuen Eintrag hinzufügen.",
                )
        return cleaned_data

    def cleaned_metadata(self) -> dict:
        metadata = {}
        for field_definition in self.metadata_fields:
            field_name = f"metadata_{field_definition.slug}"
            value = self.cleaned_data.get(field_name)
            if (
                field_definition.field_type == DocumentMetadataField.FieldType.CHOICE
                and field_definition.allow_custom_choices
            ):
                new_value = self.cleaned_data.get(f"{field_name}_new_choice", "")
                if new_value:
                    value = new_value.strip()
            if value in (None, ""):
                continue
            if field_definition.field_type == DocumentMetadataField.FieldType.DATE:
                metadata[field_definition.slug] = value.isoformat()
            elif field_definition.field_type == DocumentMetadataField.FieldType.NUMBER:
                metadata[field_definition.slug] = str(value)
            else:
                metadata[field_definition.slug] = value
        return metadata

    def custom_choice_values(self) -> dict[DocumentMetadataField, str]:
        choices = {}
        for field_definition in self.metadata_fields:
            if (
                field_definition.field_type != DocumentMetadataField.FieldType.CHOICE
                or not field_definition.allow_custom_choices
            ):
                continue
            value = self.cleaned_data.get(
                f"metadata_{field_definition.slug}_new_choice",
                "",
            ).strip()
            if not value:
                continue
            existing_values = {choice.casefold() for choice in field_definition.choices}
            if value.casefold() not in existing_values:
                choices[field_definition] = value
        return choices


class DocumentCommentForm(forms.Form):
    body = forms.CharField(
        label="Kommentar",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Hinweis zum Dokument erfassen, @ für Erwähnungen",
                "data-mention-input": "document-comment",
            }
        ),
    )


class DocumentTagForm(forms.Form):
    tag_names = forms.CharField(
        label="Tags",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "z. B. Rückfrage, dringend, geprüft",
            }
        ),
        help_text="Mehrere Tags mit Komma trennen.",
    )

    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        self.tenant = tenant
        super().__init__(*args, **kwargs)

    def clean_tag_names(self) -> list[str]:
        value = self.cleaned_data["tag_names"]
        names = []
        seen = set()
        for raw_name in value.split(","):
            name = raw_name.strip()
            if not name:
                continue
            slug = slugify(name)
            if not slug:
                raise forms.ValidationError(f"Ungültiger Tag: {name}")
            if slug in seen:
                continue
            seen.add(slug)
            names.append(name)
        return names
