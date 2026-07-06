from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from domasy.accounts.models import TenantMembership, TenantPermission, TenantRole
from domasy.documents.models import DocumentSpace
from domasy.tenancy.models import Tenant


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
