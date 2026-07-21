from __future__ import annotations

import re

from django import forms

from doksio.documents.models import DocumentSpace
from doksio.ingestion.models import ImportSource, TenantSmtpSettings
from doksio.tenancy.models import Tenant

EMAIL_SECURITY_CHOICES = [
    ("ssl", "SSL/TLS"),
    ("starttls", "STARTTLS"),
    ("none", "Keine Verschlüsselung"),
]

FOLDER_AFTER_IMPORT_CHOICES = [
    ("keep", "Datei im Quellordner belassen"),
    ("archive", "Datei in Archivordner verschieben"),
    ("delete", "Datei nach erfolgreichem Import löschen"),
]

FOLDER_RUN_MODE_CHOICES = [
    ("service", "Dauerhaft laufen und intervallweise prüfen"),
    ("once", "Einmal ausführen und beenden"),
]

EMAIL_UNPROCESSABLE_ACTION_CHOICES = [
    ("keep", "In Mailbox belassen"),
    ("mark_seen", "Als gelesen markieren"),
    ("delete", "Löschen"),
    ("move", "In Zielordner verschieben"),
]


def _lines_to_list(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


class TenantSmtpSettingsForm(forms.Form):
    host = forms.CharField(
        label="SMTP-Host",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    port = forms.IntegerField(
        label="SMTP-Port",
        required=False,
        min_value=1,
        max_value=65535,
        initial=587,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
    )
    security = forms.ChoiceField(
        label="Verschlüsselung",
        choices=TenantSmtpSettings.Security.choices,
        required=False,
        initial=TenantSmtpSettings.Security.STARTTLS,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    username = forms.CharField(
        label="Benutzername",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        label="Passwort",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control"},
            render_value=True,
        ),
    )
    from_email = forms.EmailField(
        label="Absenderadresse",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    from_name = forms.CharField(
        label="Absendername",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    is_active = forms.BooleanField(
        label="SMTP-Versand aktivieren",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    @classmethod
    def initial_from_settings(cls, settings: TenantSmtpSettings | None) -> dict:
        if settings is None:
            return {
                "port": 587,
                "security": TenantSmtpSettings.Security.STARTTLS,
            }
        return {
            "host": settings.host,
            "port": settings.port,
            "security": settings.security,
            "username": settings.username,
            "password": settings.password,
            "from_email": settings.from_email,
            "from_name": settings.from_name,
            "is_active": settings.is_active,
        }

    def clean(self) -> dict:
        cleaned_data = super().clean()
        if cleaned_data.get("is_active"):
            for field_name in ["host", "port", "from_email"]:
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, "Für aktiven SMTP-Versand erforderlich.")
        return cleaned_data


class TenantSmtpTestForm(forms.Form):
    recipient = forms.EmailField(
        label="Testmail an",
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "name@example.com",
            },
        ),
    )


class ImportSourceForm(forms.Form):
    name = forms.CharField(
        label="Name",
        max_length=160,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    source_type = forms.ChoiceField(
        label="Quelltyp",
        choices=ImportSource.SourceType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    target_strategy = forms.ChoiceField(
        label="Zielauswahl",
        choices=ImportSource.TargetStrategy.choices,
        initial=ImportSource.TargetStrategy.FIXED,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    document_space = forms.ModelChoiceField(
        label="Ziel-/Fallback-Dokumentenbox",
        queryset=DocumentSpace.objects.none(),
        help_text=(
            "Bei fester Zielauswahl ist das die Zielbox. Bei Regeln oder "
            "intelligenter Auswahl dient sie als Fallback."
        ),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    routing_rules_text = forms.CharField(
        label="Routing-Regeln",
        required=False,
        help_text=(
            "Eine Regel pro Zeile: Dateimuster => Box-Pfad. Beispiel: "
            "rechnung-*.pdf => /rechnungen."
        ),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )
    max_file_size_mb = forms.IntegerField(
        label="Maximale Dateigröße (MB)",
        required=False,
        min_value=1,
        max_value=2048,
        help_text="Leer lassen, wenn keine zusätzliche Begrenzung gelten soll.",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
    )
    allowed_content_types_text = forms.CharField(
        label="Erlaubte MIME-Typen",
        required=False,
        help_text=(
            "Ein MIME-Typ pro Zeile, z. B. application/pdf oder image/png. "
            "Leer bedeutet: alle erlauben."
        ),
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    folder_path = forms.CharField(
        label="Quellordner",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    folder_file_pattern = forms.CharField(
        label="Dateimuster",
        required=False,
        initial="*",
        help_text="Zum Beispiel *.pdf oder rechnung-*.pdf.",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    folder_recursive = forms.BooleanField(
        label="Unterordner einbeziehen",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    folder_poll_interval_seconds = forms.IntegerField(
        label="Prüfintervall (Sekunden)",
        required=False,
        min_value=30,
        initial=300,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 30}),
    )
    folder_run_mode = forms.ChoiceField(
        label="Ausführung",
        choices=FOLDER_RUN_MODE_CHOICES,
        required=False,
        initial="service",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    folder_after_import = forms.ChoiceField(
        label="Nach erfolgreichem Import",
        choices=FOLDER_AFTER_IMPORT_CHOICES,
        required=False,
        initial="archive",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    folder_archive_path = forms.CharField(
        label="Archivordner",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    folder_error_path = forms.CharField(
        label="Fehlerordner",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_host = forms.CharField(
        label="IMAP-Host",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_port = forms.IntegerField(
        label="IMAP-Port",
        required=False,
        min_value=1,
        max_value=65535,
        initial=993,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
    )
    email_security = forms.ChoiceField(
        label="Verschlüsselung",
        choices=EMAIL_SECURITY_CHOICES,
        required=False,
        initial="ssl",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    email_username = forms.CharField(
        label="Benutzername",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_password = forms.CharField(
        label="Passwort",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control"},
            render_value=True,
        ),
    )
    email_mailbox = forms.CharField(
        label="Mailbox",
        required=False,
        initial="INBOX",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_search_criteria = forms.CharField(
        label="Suchkriterium",
        required=False,
        initial="UNSEEN",
        help_text="IMAP-Suchausdruck, z. B. UNSEEN oder ALL.",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_attachment_pattern = forms.CharField(
        label="Anhangsmuster",
        required=False,
        initial="*",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_poll_interval_seconds = forms.IntegerField(
        label="Prüfintervall (Sekunden)",
        required=False,
        min_value=60,
        initial=300,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 60}),
    )
    email_mark_seen = forms.BooleanField(
        label="Nach Import als gelesen markieren",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    email_delete_after_import = forms.BooleanField(
        label="Nach Import löschen",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    email_move_processed_to = forms.CharField(
        label="Zielordner nach Import",
        required=False,
        help_text="Optional, z. B. Archiv/Doksio.",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_success_reply_enabled = forms.BooleanField(
        label="Antwort bei erfolgreichem Import senden",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    email_success_reply_subject = forms.CharField(
        label="Betreff",
        required=False,
        initial="Ihre Dokumente wurden importiert",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_success_reply_body = forms.CharField(
        label="Antworttext",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )
    email_unprocessable_action = forms.ChoiceField(
        label="Nicht importierbare Mails",
        choices=EMAIL_UNPROCESSABLE_ACTION_CHOICES,
        required=False,
        initial="keep",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    email_unprocessable_move_to = forms.CharField(
        label="Zielordner",
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_unprocessable_reply_enabled = forms.BooleanField(
        label="Antwort bei nicht importierbarer Mail senden",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    email_unprocessable_reply_subject = forms.CharField(
        label="Betreff",
        required=False,
        initial="Ihre Mail konnte nicht importiert werden",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email_unprocessable_reply_body = forms.CharField(
        label="Antworttext",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )
    auto_start_ocr = forms.BooleanField(
        label="OCR automatisch starten",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    start_workflows = forms.BooleanField(
        label="Workflows automatisch starten",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    ocr_title_strategy = forms.ChoiceField(
        label="Dokumententitel",
        choices=ImportSource.OcrTitleStrategy.choices,
        required=False,
        initial=ImportSource.OcrTitleStrategy.AUTOMATIC,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    ocr_title_regex_search = forms.CharField(
        label="RegEx-Suche",
        required=False,
        help_text=(
            "Regulärer Ausdruck auf dem OCR-Volltext. Gruppen können in der "
            "Ersetzung verwendet werden, z. B. \\1."
        ),
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    ocr_title_regex_replace = forms.CharField(
        label="Ersetzung",
        required=False,
        help_text="Zum Beispiel Rechnung \\1 oder \\g<nummer>.",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    default_tags_text = forms.CharField(
        label="Tags",
        required=False,
        help_text="Ein Tag pro Zeile. Wird beim Import automatisch gesetzt.",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    is_active = forms.BooleanField(
        label="Aktiv",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        self.fields["document_space"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("path")

    @classmethod
    def initial_from_source(cls, source: ImportSource) -> dict:
        settings = source.settings or {}
        common = settings.get("common", {})
        title = settings.get("title", {})
        folder = settings.get("folder", {})
        email = settings.get("email", {})
        return {
            "name": source.name,
            "source_type": source.source_type,
            "target_strategy": source.target_strategy,
            "document_space": source.document_space_id,
            "routing_rules_text": cls.routing_rules_to_text(
                settings.get("routing_rules", [])
            ),
            "max_file_size_mb": common.get("max_file_size_mb"),
            "allowed_content_types_text": "\n".join(
                common.get("allowed_content_types", [])
            ),
            "folder_path": folder.get("path", ""),
            "folder_file_pattern": folder.get("file_pattern", "*"),
            "folder_recursive": folder.get("recursive", False),
            "folder_poll_interval_seconds": folder.get("poll_interval_seconds", 300),
            "folder_run_mode": folder.get("run_mode", "service"),
            "folder_after_import": folder.get("after_import", "archive"),
            "folder_archive_path": folder.get("archive_path", ""),
            "folder_error_path": folder.get("error_path", ""),
            "email_host": email.get("host", ""),
            "email_port": email.get("port", 993),
            "email_security": email.get("security", "ssl"),
            "email_username": email.get("username", ""),
            "email_password": email.get("password", ""),
            "email_mailbox": email.get("mailbox", "INBOX"),
            "email_search_criteria": email.get("search_criteria", "UNSEEN"),
            "email_attachment_pattern": email.get("attachment_pattern", "*"),
            "email_poll_interval_seconds": email.get("poll_interval_seconds", 300),
            "email_mark_seen": email.get("mark_seen", True),
            "email_delete_after_import": email.get("delete_after_import", False),
            "email_move_processed_to": email.get("move_processed_to", ""),
            "email_success_reply_enabled": email.get(
                "success_reply_enabled",
                False,
            ),
            "email_success_reply_subject": email.get(
                "success_reply_subject",
                "Ihre Dokumente wurden importiert",
            ),
            "email_success_reply_body": email.get("success_reply_body", ""),
            "email_unprocessable_action": email.get(
                "unprocessable_action",
                "keep",
            ),
            "email_unprocessable_move_to": email.get(
                "unprocessable_move_to",
                "",
            ),
            "email_unprocessable_reply_enabled": email.get(
                "unprocessable_reply_enabled",
                False,
            ),
            "email_unprocessable_reply_subject": email.get(
                "unprocessable_reply_subject",
                "Ihre Mail konnte nicht importiert werden",
            ),
            "email_unprocessable_reply_body": email.get(
                "unprocessable_reply_body",
                "",
            ),
            "auto_start_ocr": source.auto_start_ocr,
            "start_workflows": source.start_workflows,
            "ocr_title_strategy": title.get(
                "strategy",
                ImportSource.OcrTitleStrategy.AUTOMATIC,
            ),
            "ocr_title_regex_search": title.get("regex_search", ""),
            "ocr_title_regex_replace": title.get("regex_replace", ""),
            "default_tags_text": "\n".join(source.default_tags),
            "is_active": source.is_active,
        }

    def clean_default_tags_text(self) -> list[str]:
        raw_value = self.cleaned_data.get("default_tags_text", "")
        return _lines_to_list(raw_value)

    @classmethod
    def routing_rules_to_text(cls, rules: list[dict]) -> str:
        lines = []
        for rule in rules:
            pattern = rule.get("pattern", "")
            document_space_path = rule.get("document_space_path", "")
            if pattern and document_space_path:
                lines.append(f"{pattern} => {document_space_path}")
        return "\n".join(lines)

    def clean_routing_rules_text(self) -> list[dict]:
        raw_value = self.cleaned_data.get("routing_rules_text", "")
        rules = []
        spaces_by_path = {
            space.path: space
            for space in DocumentSpace.objects.filter(
                tenant=self.tenant,
                is_active=True,
            )
        }
        for line_number, line in enumerate(_lines_to_list(raw_value), start=1):
            if "=>" in line:
                pattern, document_space_path = line.split("=>", 1)
            elif "|" in line:
                pattern, document_space_path = line.split("|", 1)
            else:
                raise forms.ValidationError(
                    f"Zeile {line_number}: Bitte 'Muster => /box-pfad' verwenden."
                )

            pattern = pattern.strip()
            document_space_path = document_space_path.strip()
            if not pattern or not document_space_path:
                raise forms.ValidationError(
                    f"Zeile {line_number}: Muster und Box-Pfad sind erforderlich."
                )
            document_space = spaces_by_path.get(document_space_path)
            if document_space is None:
                raise forms.ValidationError(
                    
                        f"Zeile {line_number}: Dokumentenbox "
                        f"{document_space_path} existiert nicht."
                    
                )
            rules.append(
                {
                    "pattern": pattern,
                    "document_space_id": document_space.id,
                    "document_space_path": document_space.path,
                }
            )
        return rules

    def clean(self) -> dict:
        cleaned_data = super().clean()
        if not cleaned_data.get("ocr_title_strategy"):
            cleaned_data["ocr_title_strategy"] = ImportSource.OcrTitleStrategy.AUTOMATIC
        source_type = cleaned_data.get("source_type")
        target_strategy = cleaned_data.get("target_strategy")
        if (
            target_strategy == ImportSource.TargetStrategy.RULES
            and not cleaned_data.get("routing_rules_text")
        ):
            self.add_error(
                "routing_rules_text",
                "Für regelbasierte Zielauswahl ist mindestens eine Regel erforderlich.",
            )

        if source_type == ImportSource.SourceType.FOLDER:
            if not cleaned_data.get("folder_path"):
                self.add_error("folder_path", "Der Quellordner ist erforderlich.")
            archives_after_import = cleaned_data.get("folder_after_import") == "archive"
            if archives_after_import and not cleaned_data.get("folder_archive_path"):
                self.add_error(
                    "folder_archive_path",
                    "Für Archivierung ist ein Archivordner erforderlich.",
                )

        if source_type == ImportSource.SourceType.EMAIL:
            for field_name in [
                "email_host",
                "email_port",
                "email_username",
                "email_password",
                "email_mailbox",
            ]:
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, "Für E-Mail-Import erforderlich.")
            if (
                cleaned_data.get("email_success_reply_enabled")
                and not cleaned_data.get("email_success_reply_body")
            ):
                self.add_error(
                    "email_success_reply_body",
                    "Für automatische Antworten ist ein Antworttext erforderlich.",
                )
            if (
                cleaned_data.get("email_unprocessable_action") == "move"
                and not cleaned_data.get("email_unprocessable_move_to")
            ):
                self.add_error(
                    "email_unprocessable_move_to",
                    "Für Verschieben ist ein Zielordner erforderlich.",
                )
            if (
                cleaned_data.get("email_unprocessable_reply_enabled")
                and not cleaned_data.get("email_unprocessable_reply_body")
            ):
                self.add_error(
                    "email_unprocessable_reply_body",
                    "Für automatische Antworten ist ein Antworttext erforderlich.",
                )

        if (
            cleaned_data.get("ocr_title_strategy")
            == ImportSource.OcrTitleStrategy.REGEX
        ):
            regex_search = cleaned_data.get("ocr_title_regex_search", "")
            if not regex_search:
                self.add_error(
                    "ocr_title_regex_search",
                    "Für RegEx-Titel ist ein Suchmuster erforderlich.",
                )
            else:
                try:
                    re.compile(regex_search)
                except re.error as error:
                    self.add_error(
                        "ocr_title_regex_search",
                        f"Ungültiger regulärer Ausdruck: {error}",
                    )

        return cleaned_data

    @property
    def import_settings(self) -> dict:
        if not self.is_valid():
            return {}

        common = {
            "max_file_size_mb": self.cleaned_data.get("max_file_size_mb"),
            "allowed_content_types": _lines_to_list(
                self.cleaned_data.get("allowed_content_types_text", "")
            ),
        }
        title_settings = {
            "strategy": (
                self.cleaned_data.get("ocr_title_strategy")
                or ImportSource.OcrTitleStrategy.AUTOMATIC
            ),
            "regex_search": self.cleaned_data.get("ocr_title_regex_search", ""),
            "regex_replace": self.cleaned_data.get("ocr_title_regex_replace", ""),
        }
        routing_rules = self.cleaned_data["routing_rules_text"]
        settings = {
            "title": title_settings,
        }
        if routing_rules:
            settings["routing_rules"] = routing_rules
        source_type = self.cleaned_data["source_type"]
        if source_type == ImportSource.SourceType.HTTP_API:
            return settings | {"common": common}
        if source_type == ImportSource.SourceType.FOLDER:
            return settings | {
                "common": common,
                "folder": {
                    "path": self.cleaned_data["folder_path"],
                    "file_pattern": self.cleaned_data.get("folder_file_pattern") or "*",
                    "recursive": self.cleaned_data["folder_recursive"],
                    "poll_interval_seconds": self.cleaned_data[
                        "folder_poll_interval_seconds"
                    ],
                    "run_mode": self.cleaned_data["folder_run_mode"],
                    "after_import": self.cleaned_data["folder_after_import"],
                    "archive_path": self.cleaned_data["folder_archive_path"],
                    "error_path": self.cleaned_data["folder_error_path"],
                },
            }
        if source_type == ImportSource.SourceType.EMAIL:
            return settings | {
                "common": common,
                "email": {
                    "host": self.cleaned_data["email_host"],
                    "port": self.cleaned_data["email_port"],
                    "security": self.cleaned_data["email_security"],
                    "username": self.cleaned_data["email_username"],
                    "password": self.cleaned_data["email_password"],
                    "mailbox": self.cleaned_data["email_mailbox"],
                    "search_criteria": self.cleaned_data["email_search_criteria"],
                    "attachment_pattern": self.cleaned_data[
                        "email_attachment_pattern"
                    ]
                    or "*",
                    "poll_interval_seconds": self.cleaned_data[
                        "email_poll_interval_seconds"
                    ],
                    "mark_seen": self.cleaned_data["email_mark_seen"],
                    "delete_after_import": self.cleaned_data[
                        "email_delete_after_import"
                    ],
                    "move_processed_to": self.cleaned_data["email_move_processed_to"],
                    "success_reply_enabled": self.cleaned_data[
                        "email_success_reply_enabled"
                    ],
                    "success_reply_subject": self.cleaned_data[
                        "email_success_reply_subject"
                    ],
                    "success_reply_body": self.cleaned_data[
                        "email_success_reply_body"
                    ],
                    "unprocessable_action": self.cleaned_data[
                        "email_unprocessable_action"
                    ],
                    "unprocessable_move_to": self.cleaned_data[
                        "email_unprocessable_move_to"
                    ],
                    "unprocessable_reply_enabled": self.cleaned_data[
                        "email_unprocessable_reply_enabled"
                    ],
                    "unprocessable_reply_subject": self.cleaned_data[
                        "email_unprocessable_reply_subject"
                    ],
                    "unprocessable_reply_body": self.cleaned_data[
                        "email_unprocessable_reply_body"
                    ],
                },
            }
        return settings
