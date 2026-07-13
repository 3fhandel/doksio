from __future__ import annotations

from django import forms
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.utils.text import slugify

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.metadata import metadata_field_slug_is_available
from doksio.documents.models import DocumentMetadataField, DocumentSpace
from doksio.documents.policies import filter_document_spaces_for_user
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
        help_text=(
            "Optional, wenn für Uploads eine Importstrategie hinterlegt ist."
        ),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    file = MultipleFileField(
        label="Dateien",
        widget=MultipleFileInput(
            attrs={"class": "form-control", "multiple": True},
        ),
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
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
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
        self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(
            path__startswith=f"{document_space.path.rstrip('/')}/",
        ).exclude(id=document_space.id)

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
                "Dieser Metadaten-Slug ist bereits in einer Eltern- oder Kindbox vergeben."
            )
        return slug

    def clean(self) -> dict:
        cleaned_data = super().clean()
        field_type = cleaned_data.get("field_type")
        choices_text = cleaned_data.get("choices_text", "")
        choices = [
            choice.strip()
            for choice in choices_text.splitlines()
            if choice.strip()
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
                widget=forms.DateInput(attrs={**attrs, "type": "date"}),
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
            elif (
                field_definition.field_type
                == DocumentMetadataField.FieldType.NUMBER
            ):
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
