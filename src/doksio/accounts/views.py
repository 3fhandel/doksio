from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode

from doksio.accounts.forms import (
    KEYBOARD_SHORTCUT_ACTIONS,
    StyledSetPasswordForm,
    SystemLoginForm,
    TenantLoginForm,
    UserProfileForm,
    notification_preferences_from_form,
)
from doksio.accounts.models import Notification, UserProfile
from doksio.accounts.oidc import (
    build_oidc_authorization_url,
    exchange_oidc_code,
    fetch_oidc_userinfo,
    oidc_enabled,
    pop_oidc_login_context,
    tenant_slugs_from_oidc_claims,
    user_from_oidc_claims,
)
from doksio.accounts.services import MarkAllNotificationsRead, MarkNotificationRead
from doksio.documents.policies import can_administer_tenant
from doksio.tenancy.models import Tenant
from doksio.tenancy.services import get_tenant_for_user


def _safe_next_url(request: HttpRequest, fallback: str) -> str:
    next_url = request.POST.get("next") or request.GET.get("next")
    return _safe_url(request, next_url, fallback)


def _safe_url(request: HttpRequest, next_url: str | None, fallback: str) -> str:
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def system_login(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect("admin:index")

    if request.method == "POST":
        form = SystemLoginForm(request=request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(_safe_next_url(request, reverse("admin:index")))
    else:
        form = SystemLoginForm(request=request)

    return render(
        request,
        "accounts/system_login.html",
        {
            "form": form,
            "next": request.GET.get("next", ""),
            "oidc_enabled": oidc_enabled(),
        },
    )


def tenant_login(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    tenant = get_object_or_404(Tenant, slug=tenant_slug, is_active=True)
    fallback_url = reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})

    if request.user.is_authenticated and get_tenant_for_user(request.user, tenant.slug):
        return redirect(_safe_next_url(request, fallback_url))

    if request.method == "POST":
        form = TenantLoginForm(request=request, tenant=tenant, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(_safe_next_url(request, fallback_url))
    else:
        form = TenantLoginForm(request=request, tenant=tenant)

    return render(
        request,
        "accounts/tenant_login.html",
        {
            "form": form,
            "tenant": tenant,
            "next": request.GET.get("next", ""),
            "oidc_enabled": oidc_enabled(),
        },
    )


def sign_out(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("accounts:system_login")


def system_oidc_login(request: HttpRequest) -> HttpResponse:
    return redirect(
        build_oidc_authorization_url(
            request=request,
            mode="system",
            next_url=request.GET.get("next", ""),
        )
    )


def tenant_claim_oidc_login(request: HttpRequest) -> HttpResponse:
    return redirect(
        build_oidc_authorization_url(
            request=request,
            mode="tenant_claim",
            next_url=request.GET.get("next", ""),
        )
    )


def tenant_oidc_login(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    tenant = get_object_or_404(Tenant, slug=tenant_slug, is_active=True)
    return redirect(
        build_oidc_authorization_url(
            request=request,
            mode="tenant",
            tenant_slug=tenant.slug,
            next_url=request.GET.get("next", ""),
        )
    )


def oidc_callback(request: HttpRequest) -> HttpResponse:
    error = request.GET.get("error")
    if error:
        messages.error(request, f"OIDC-Anmeldung fehlgeschlagen: {error}")
        return redirect("accounts:system_login")

    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    context = pop_oidc_login_context(request, state)
    if not code:
        raise PermissionDenied("OIDC-Callback ohne Code.")

    token_response = exchange_oidc_code(code)
    access_token = token_response.get("access_token")
    if not access_token:
        raise PermissionDenied("OIDC-Token-Antwort ohne Access Token.")
    claims = fetch_oidc_userinfo(access_token)
    user = user_from_oidc_claims(claims)

    if context.mode == "system":
        if not user.is_superuser:
            raise PermissionDenied
        login(request, user)
        return redirect(_safe_url(request, context.next_url, reverse("admin:index")))

    tenant = _tenant_from_oidc_context(user, context, claims)
    login(request, user)
    fallback_url = reverse("documents:dashboard", kwargs={"tenant_slug": tenant.slug})
    return redirect(_safe_url(request, context.next_url, fallback_url))


def _tenant_from_oidc_context(user, context, claims: dict) -> Tenant:
    if context.tenant_slug:
        tenant = get_object_or_404(
            Tenant,
            slug=context.tenant_slug,
            is_active=True,
        )
        if get_tenant_for_user(user, tenant.slug) is None:
            raise PermissionDenied
        return tenant

    tenant_slugs = tenant_slugs_from_oidc_claims(claims)
    if not tenant_slugs:
        raise PermissionDenied("OIDC-Antwort enthält keinen Tenant-Hinweis.")

    matching_tenants = []
    for tenant_slug in tenant_slugs:
        tenant = Tenant.objects.filter(slug=tenant_slug, is_active=True).first()
        if tenant and get_tenant_for_user(user, tenant.slug) is not None:
            matching_tenants.append(tenant)

    if len(matching_tenants) == 1:
        return matching_tenants[0]
    if len(matching_tenants) > 1:
        raise PermissionDenied("OIDC-Antwort passt zu mehreren Tenants.")
    raise PermissionDenied("Benutzer hat keinen Zugriff auf den OIDC-Tenant.")


def tenant_password_reset_confirm(
    request: HttpRequest,
    tenant_slug: str,
    uidb64: str,
    token: str,
) -> HttpResponse:
    tenant = get_object_or_404(Tenant, slug=tenant_slug, is_active=True)
    user_model = get_user_model()
    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = user_model.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, user_model.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        return render(
            request,
            "accounts/password_reset_invalid.html",
            {
                "tenant": tenant,
            },
            status=400,
        )

    if request.method == "POST":
        form = StyledSetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Das Passwort wurde geändert. Du kannst dich jetzt anmelden.",
            )
            return redirect("accounts:tenant_login", tenant_slug=tenant.slug)
    else:
        form = StyledSetPasswordForm(user)

    return render(
        request,
        "accounts/password_reset_confirm.html",
        {
            "tenant": tenant,
            "form": form,
        },
    )


def _profile_context(
    request: HttpRequest,
    tenant_slug: str,
) -> tuple[Tenant, UserProfile]:
    if not request.user.is_authenticated:
        login_url = reverse(
            "accounts:tenant_login",
            kwargs={"tenant_slug": tenant_slug},
        )
        raise PermissionDenied(f"{login_url}?next={request.get_full_path()}")

    tenant = get_tenant_for_user(request.user, tenant_slug)
    if tenant is None:
        raise PermissionDenied

    user_profile, _created = UserProfile.objects.get_or_create(user=request.user)
    return tenant, user_profile


def _redirect_to_login(request: HttpRequest, tenant_slug: str) -> HttpResponse | None:
    if request.user.is_authenticated:
        return None
    login_url = reverse(
        "accounts:tenant_login",
        kwargs={"tenant_slug": tenant_slug},
    )
    return redirect(f"{login_url}?next={request.get_full_path()}")


def profile(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    return redirect("accounts:profile_account", tenant_slug=tenant_slug)


def profile_account(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_redirect = _redirect_to_login(request, tenant_slug)
    if login_redirect is not None:
        return login_redirect

    tenant, user_profile = _profile_context(request, tenant_slug)
    if request.method == "POST":
        form = UserProfileForm(request.POST, profile=user_profile)
        if form.is_valid():
            request.user.email = form.cleaned_data["email"]
            user_update_fields = ["email"]
            new_password = form.cleaned_data.get("new_password1")
            if new_password:
                request.user.set_password(new_password)
                user_update_fields.append("password")
            request.user.save(update_fields=user_update_fields)
            if new_password:
                update_session_auth_hash(request, request.user)

            user_profile.display_name = form.cleaned_data["display_name"]
            user_profile.save(
                update_fields=[
                    "display_name",
                    "updated_at",
                ]
            )
            messages.success(request, "Account wurde gespeichert.")
            return redirect("accounts:profile_account", tenant_slug=tenant.slug)
    else:
        form = UserProfileForm(profile=user_profile)

    return render(
        request,
        "accounts/profile_account.html",
        {
            "tenant": tenant,
            "form": form,
            "active_profile_section": "account",
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )


def profile_notifications(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_redirect = _redirect_to_login(request, tenant_slug)
    if login_redirect is not None:
        return login_redirect

    tenant, user_profile = _profile_context(request, tenant_slug)
    if request.method == "POST":
        action = request.POST.get("action", "save_settings")
        if action == "mark_read":
            notification = get_object_or_404(
                Notification,
                tenant=tenant,
                recipient=request.user,
                id=request.POST.get("notification_id"),
            )
            MarkNotificationRead(
                notification=notification,
                actor=request.user,
            ).execute()
            messages.success(request, "Benachrichtigung wurde als gelesen markiert.")
            return redirect("accounts:profile_notifications", tenant_slug=tenant.slug)
        if action == "mark_all_read":
            MarkAllNotificationsRead(tenant=tenant, actor=request.user).execute()
            messages.success(request, "Benachrichtigungen wurden als gelesen markiert.")
            return redirect("accounts:profile_notifications", tenant_slug=tenant.slug)

        form = UserProfileForm(request.POST, profile=user_profile)
        if form.is_valid():
            notification_preferences = notification_preferences_from_form(form)
            user_profile.notifications_enabled = any(
                channels["in_app"]
                for channels in notification_preferences.values()
            )
            user_profile.workflow_notifications_enabled = notification_preferences[
                Notification.Type.WORKFLOW_TASK_CREATED
            ]["in_app"]
            user_profile.mention_notifications_enabled = notification_preferences[
                Notification.Type.DOCUMENT_COMMENT_MENTION
            ]["in_app"]
            user_profile.notification_preferences = notification_preferences
            user_profile.save(
                update_fields=[
                    "notifications_enabled",
                    "workflow_notifications_enabled",
                    "mention_notifications_enabled",
                    "notification_preferences",
                    "updated_at",
                ]
            )
            messages.success(request, "Benachrichtigungen wurden gespeichert.")
            return redirect("accounts:profile_notifications", tenant_slug=tenant.slug)
    else:
        form = UserProfileForm(profile=user_profile)

    notifications = Notification.objects.filter(
        tenant=tenant,
        recipient=request.user,
    ).select_related("document", "workflow_task")[:25]
    unread_notifications_count = Notification.objects.filter(
        tenant=tenant,
        recipient=request.user,
        read_at__isnull=True,
    ).count()

    return render(
        request,
        "accounts/profile_notifications.html",
        {
            "tenant": tenant,
            "form": form,
            "active_profile_section": "notifications",
            "can_manage_settings": can_administer_tenant(request.user, tenant),
            "notifications": notifications,
            "profile_unread_notifications_count": unread_notifications_count,
        },
    )


def profile_shortcuts(request: HttpRequest, tenant_slug: str) -> HttpResponse:
    login_redirect = _redirect_to_login(request, tenant_slug)
    if login_redirect is not None:
        return login_redirect

    tenant, user_profile = _profile_context(request, tenant_slug)
    if request.method == "POST":
        form = UserProfileForm(request.POST, profile=user_profile)
        if form.is_valid():
            user_profile.keyboard_shortcuts = form.keyboard_shortcuts()
            user_profile.save(
                update_fields=[
                    "keyboard_shortcuts",
                    "updated_at",
                ]
            )
            messages.success(request, "Tastenkürzel wurden gespeichert.")
            return redirect("accounts:profile_shortcuts", tenant_slug=tenant.slug)
    else:
        form = UserProfileForm(profile=user_profile)

    return render(
        request,
        "accounts/profile_shortcuts.html",
        {
            "tenant": tenant,
            "form": form,
            "shortcut_actions": KEYBOARD_SHORTCUT_ACTIONS,
            "active_profile_section": "shortcuts",
            "can_manage_settings": can_administer_tenant(request.user, tenant),
        },
    )
