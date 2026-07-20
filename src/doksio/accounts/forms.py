from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password

from doksio.accounts.models import (
    TenantMembership,
    TenantPermission,
    TenantRole,
    UserProfile,
    default_keyboard_shortcuts,
)
from doksio.documents.models import DocumentSpace
from doksio.tenancy.models import Tenant

KEYBOARD_SHORTCUT_ACTIONS = [
    ("dashboard", "Dashboard öffnen"),
    ("tasks", "Meine Aufgaben öffnen"),
    ("documents", "Dokumente öffnen"),
    ("search", "Suche öffnen"),
    ("upload", "Dokument hochladen"),
    ("document_previous", "Vorheriges Dokument"),
    ("document_next", "Nächstes Dokument"),
    ("document_back", "Zurück aus Dokument"),
    ("document_edit_core", "Kerndaten bearbeiten"),
    ("workflow_complete", "Workflow-Aufgabe erledigen"),
]

SPECIAL_KEYS = {
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "backspace": "Backspace",
    "enter": "Enter",
    "escape": "Escape",
    "esc": "Escape",
    "space": "Space",
}


def normalize_keyboard_shortcut(value: str) -> str:
    parts = [part.strip() for part in value.split("+") if part.strip()]
    if not parts:
        return ""

    modifiers = []
    key = ""
    for part in parts:
        lowered = part.lower()
        if lowered in {"ctrl", "control"}:
            modifier = "Ctrl"
        elif lowered in {"alt", "option"}:
            modifier = "Alt"
        elif lowered == "shift":
            modifier = "Shift"
        elif lowered in {"meta", "cmd", "command"}:
            modifier = "Meta"
        else:
            if key:
                raise forms.ValidationError(
                    "Bitte nur eine Haupttaste pro Tastenkürzel verwenden."
                )
            key = SPECIAL_KEYS.get(lowered, part.upper() if len(part) == 1 else part)
            continue
        if modifier not in modifiers:
            modifiers.append(modifier)

    if not key:
        raise forms.ValidationError("Bitte eine Taste zusätzlich zum Modifier angeben.")
    if not modifiers:
        raise forms.ValidationError(
            "Bitte mindestens Strg, Alt, Shift oder Meta verwenden."
        )
    return "+".join([*modifiers, key])


class StyledAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "class": "form-control",
            }
        )
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": "form-control",
            }
        ),
    )


class SystemLoginForm(StyledAuthenticationForm):
    error_messages = {
        **StyledAuthenticationForm.error_messages,
        "not_system_admin": "Dieser Zugang ist nur für System-Admins.",
    }

    def confirm_login_allowed(self, user) -> None:
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise forms.ValidationError(
                self.error_messages["not_system_admin"],
                code="not_system_admin",
            )


class TenantLoginForm(StyledAuthenticationForm):
    error_messages = {
        **StyledAuthenticationForm.error_messages,
        "not_tenant_member": "Dieser Benutzer hat keinen Zugriff auf diesen Mandanten.",
    }

    def __init__(self, request=None, *, tenant: Tenant, **kwargs) -> None:
        self.tenant = tenant
        super().__init__(request=request, **kwargs)

    def confirm_login_allowed(self, user) -> None:
        super().confirm_login_allowed(user)
        if user.is_superuser:
            return

        has_membership = TenantMembership.objects.filter(
            user=user,
            tenant=self.tenant,
            is_active=True,
            tenant__is_active=True,
        ).exists()
        if not has_membership:
            raise forms.ValidationError(
                self.error_messages["not_tenant_member"],
                code="not_tenant_member",
            )


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, user, *args, **kwargs) -> None:
        super().__init__(user, *args, **kwargs)
        self.fields["new_password1"].widget.attrs.update(
            {
                "class": "form-control",
                "autocomplete": "new-password",
            }
        )
        self.fields["new_password2"].widget.attrs.update(
            {
                "class": "form-control",
                "autocomplete": "new-password",
            }
        )


class UserProfileForm(forms.Form):
    display_name = forms.CharField(
        label="Anzeigename",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    first_name = forms.CharField(
        label="Vorname",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        label="Nachname",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email = forms.EmailField(
        label="E-Mail-Adresse",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    current_password = forms.CharField(
        label="Aktuelles Passwort",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": "form-control",
            }
        ),
    )
    new_password1 = forms.CharField(
        label="Neues Passwort",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "form-control",
            }
        ),
    )
    new_password2 = forms.CharField(
        label="Neues Passwort wiederholen",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "class": "form-control",
            }
        ),
    )
    notifications_enabled = forms.BooleanField(
        label="Benachrichtigungen aktivieren",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    mention_notifications_enabled = forms.BooleanField(
        label="Benachrichtigung, wenn ich erwähnt wurde",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, profile: UserProfile, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.profile = profile
        self.user = profile.user
        self.is_identity_provider_managed = bool(profile.oidc_subject)
        if self.is_identity_provider_managed:
            self.fields.pop("current_password")
            self.fields.pop("new_password1")
            self.fields.pop("new_password2")
        shortcuts = {
            **default_keyboard_shortcuts(),
            **(profile.keyboard_shortcuts or {}),
        }
        for action, label in KEYBOARD_SHORTCUT_ACTIONS:
            self.fields[f"shortcut_{action}"] = forms.CharField(
                label=label,
                required=False,
                initial=shortcuts.get(action, ""),
                widget=forms.TextInput(
                    attrs={
                        "class": "form-control form-control-sm",
                        "autocomplete": "off",
                        "data-shortcut-capture": "true",
                        "inputmode": "none",
                        "placeholder": "z. B. Alt+U",
                        "spellcheck": "false",
                    }
                ),
            )
        if not self.is_bound:
            self.initial["display_name"] = profile.display_name
            self.initial["first_name"] = self.user.first_name
            self.initial["last_name"] = self.user.last_name
            self.initial["email"] = self.user.email
            self.initial["notifications_enabled"] = profile.notifications_enabled
            self.initial["mention_notifications_enabled"] = (
                profile.mention_notifications_enabled
            )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        if self.is_identity_provider_managed:
            return self._clean_keyboard_shortcuts(cleaned_data)

        current_password = cleaned_data.get("current_password", "")
        new_password1 = cleaned_data.get("new_password1", "")
        new_password2 = cleaned_data.get("new_password2", "")
        wants_password_change = any([current_password, new_password1, new_password2])
        if wants_password_change:
            if not current_password:
                self.add_error(
                    "current_password",
                    "Bitte das aktuelle Passwort eingeben.",
                )
            elif not self.user.check_password(current_password):
                self.add_error(
                    "current_password",
                    "Das aktuelle Passwort ist nicht korrekt.",
                )
            if not new_password1:
                self.add_error("new_password1", "Bitte ein neues Passwort eingeben.")
            if not new_password2:
                self.add_error(
                    "new_password2",
                    "Bitte das neue Passwort wiederholen.",
                )
            if new_password1 and new_password2 and new_password1 != new_password2:
                self.add_error(
                    "new_password2",
                    "Die neuen Passwörter stimmen nicht überein.",
                )
            if new_password1:
                try:
                    validate_password(new_password1, self.user)
                except forms.ValidationError as error:
                    self.add_error("new_password1", error)

        return self._clean_keyboard_shortcuts(cleaned_data)

    def _clean_keyboard_shortcuts(self, cleaned_data: dict) -> dict:
        seen_shortcuts = {}
        for action, label in KEYBOARD_SHORTCUT_ACTIONS:
            field_name = f"shortcut_{action}"
            value = cleaned_data.get(field_name, "")
            if not value:
                continue
            try:
                normalized_value = normalize_keyboard_shortcut(value)
            except forms.ValidationError as error:
                self.add_error(field_name, error)
                continue
            cleaned_data[field_name] = normalized_value
            if normalized_value in seen_shortcuts:
                duplicate_label = seen_shortcuts[normalized_value]
                self.add_error(
                    field_name,
                    f"Dieses Tastenkürzel ist bereits für {duplicate_label} vergeben.",
                )
            seen_shortcuts[normalized_value] = label
        return cleaned_data

    def keyboard_shortcuts(self) -> dict[str, str]:
        return {
            action: self.cleaned_data.get(f"shortcut_{action}", "")
            for action, _label in KEYBOARD_SHORTCUT_ACTIONS
            if self.cleaned_data.get(f"shortcut_{action}", "")
        }


class TenantMembershipCreateForm(forms.Form):
    username = forms.CharField(
        label="Benutzername",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email = forms.EmailField(
        label="E-Mail",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    display_name = forms.CharField(
        label="Anzeigename",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    first_name = forms.CharField(
        label="Vorname",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        label="Nachname",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        label="Passwort",
        required=False,
        help_text="Erforderlich, wenn der Benutzer neu angelegt wird.",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    roles = forms.ModelMultipleChoiceField(
        label="Rollen",
        queryset=TenantRole.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields["roles"].queryset = TenantRole.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("name")

    def clean(self) -> dict:
        cleaned_data = super().clean()
        username = cleaned_data.get("username")
        password = cleaned_data.get("password")
        if (
            username
            and not get_user_model().objects.filter(username=username).exists()
            and not password
        ):
            self.add_error(
                "password",
                "Für neue Benutzer muss ein Passwort vergeben werden.",
            )
        roles = list(cleaned_data.get("roles") or [])
        legacy_role_id = self.data.get("role")
        if not roles and legacy_role_id:
            role = TenantRole.objects.filter(
                tenant=self.tenant,
                is_active=True,
                id=legacy_role_id,
            ).first()
            if role:
                roles = [role]
                cleaned_data["roles"] = roles
        if not roles:
            self.add_error("roles", "Bitte mindestens eine Rolle auswählen.")
        return cleaned_data


class TenantMembershipUpdateForm(forms.Form):
    display_name = forms.CharField(
        label="Anzeigename",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )
    first_name = forms.CharField(
        label="Vorname",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )
    last_name = forms.CharField(
        label="Nachname",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )
    email = forms.EmailField(
        label="E-Mail",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control form-control-sm"}),
    )
    password = forms.CharField(
        label="Neues Passwort",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control form-control-sm"}),
    )
    roles = forms.ModelMultipleChoiceField(
        label="Rollen",
        queryset=TenantRole.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    is_active = forms.BooleanField(
        label="Aktiv",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields["roles"].queryset = TenantRole.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("name")

    def clean(self) -> dict:
        cleaned_data = super().clean()
        roles = list(cleaned_data.get("roles") or [])
        legacy_role_id = self.data.get("role")
        if not roles and legacy_role_id:
            role = TenantRole.objects.filter(
                tenant=self.tenant,
                is_active=True,
                id=legacy_role_id,
            ).first()
            if role:
                roles = [role]
                cleaned_data["roles"] = roles
        if not roles:
            self.add_error("roles", "Bitte mindestens eine Rolle auswählen.")
        return cleaned_data


class TenantRoleCreateForm(forms.Form):
    name = forms.CharField(
        label="Name",
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    slug = forms.SlugField(
        label="Slug",
        max_length=80,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    description = forms.CharField(
        label="Beschreibung",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    permissions = forms.ModelMultipleChoiceField(
        label="Berechtigungen",
        queryset=TenantPermission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    can_access_all_document_spaces = forms.BooleanField(
        label="Darf auf alle Dokumentenboxen zugreifen",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    document_spaces = forms.ModelMultipleChoiceField(
        label="Dokumentenbox-Zugriff",
        queryset=DocumentSpace.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Nur relevant, wenn der Zugriff nicht für alle Dokumentenboxen gilt."
        ),
    )

    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = TenantPermission.objects.order_by(
            "category",
            "sort_order",
            "code",
        )
        self.fields["document_spaces"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("path")

    def clean_slug(self) -> str:
        slug = self.cleaned_data["slug"]
        if TenantRole.objects.filter(tenant=self.tenant, slug=slug).exists():
            raise forms.ValidationError("Dieser Rollen-Slug existiert bereits.")
        return slug


class TenantRoleUpdateForm(forms.Form):
    name = forms.CharField(
        label="Name",
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}),
    )
    description = forms.CharField(
        label="Beschreibung",
        required=False,
        widget=forms.Textarea(
            attrs={"class": "form-control form-control-sm", "rows": 2}
        ),
    )
    permissions = forms.ModelMultipleChoiceField(
        label="Berechtigungen",
        queryset=TenantPermission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    can_access_all_document_spaces = forms.BooleanField(
        label="Darf auf alle Dokumentenboxen zugreifen",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    document_spaces = forms.ModelMultipleChoiceField(
        label="Dokumentenbox-Zugriff",
        queryset=DocumentSpace.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Nur relevant, wenn der Zugriff nicht für alle Dokumentenboxen gilt."
        ),
    )
    is_active = forms.BooleanField(
        label="Aktiv",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, tenant: Tenant, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = TenantPermission.objects.order_by(
            "category",
            "sort_order",
            "code",
        )
        self.fields["document_spaces"].queryset = DocumentSpace.objects.filter(
            tenant=tenant,
            is_active=True,
        ).order_by("path")
