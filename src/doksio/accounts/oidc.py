from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.urls import reverse

from doksio.accounts.models import UserProfile
from doksio.project.url_helpers import build_public_url

OIDC_SESSION_KEY = "doksio_oidc_login"


@dataclass(frozen=True)
class OidcLoginContext:
    state: str
    nonce: str
    mode: str
    tenant_slug: str
    next_url: str


def oidc_enabled() -> bool:
    return bool(settings.DOKSIO_OIDC_ENABLED)


def require_oidc_enabled() -> None:
    if not oidc_enabled():
        raise PermissionDenied
    required_settings = [
        "DOKSIO_OIDC_ISSUER_URL",
        "DOKSIO_OIDC_CLIENT_ID",
        "DOKSIO_OIDC_CLIENT_SECRET",
    ]
    missing = [name for name in required_settings if not getattr(settings, name)]
    if missing:
        raise ImproperlyConfigured(
            "OIDC ist aktiviert, aber nicht vollständig konfiguriert: "
            + ", ".join(missing)
        )


def oidc_callback_url() -> str:
    return build_public_url(reverse("accounts:oidc_callback"))


@lru_cache(maxsize=8)
def oidc_discovery() -> dict:
    require_oidc_enabled()
    url = f"{settings.DOKSIO_OIDC_ISSUER_URL}/.well-known/openid-configuration"
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def build_oidc_authorization_url(
    *,
    request,
    mode: str,
    tenant_slug: str = "",
    next_url: str = "",
) -> str:
    discovery = oidc_discovery()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    request.session[OIDC_SESSION_KEY] = {
        "state": state,
        "nonce": nonce,
        "mode": mode,
        "tenant_slug": tenant_slug,
        "next_url": next_url,
    }
    request.session.modified = True
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.DOKSIO_OIDC_CLIENT_ID,
            "redirect_uri": oidc_callback_url(),
            "scope": settings.DOKSIO_OIDC_SCOPE,
            "state": state,
            "nonce": nonce,
        }
    )
    return f"{discovery['authorization_endpoint']}?{query}"


def pop_oidc_login_context(request, state: str) -> OidcLoginContext:
    raw_context = request.session.pop(OIDC_SESSION_KEY, None)
    request.session.modified = True
    if not raw_context or raw_context.get("state") != state:
        raise PermissionDenied("Ungültiger OIDC-State.")
    return OidcLoginContext(
        state=raw_context["state"],
        nonce=raw_context["nonce"],
        mode=raw_context.get("mode", "tenant"),
        tenant_slug=raw_context.get("tenant_slug", ""),
        next_url=raw_context.get("next_url", ""),
    )


def exchange_oidc_code(code: str) -> dict:
    discovery = oidc_discovery()
    payload = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": oidc_callback_url(),
            "client_id": settings.DOKSIO_OIDC_CLIENT_ID,
            "client_secret": settings.DOKSIO_OIDC_CLIENT_SECRET,
        }
    ).encode("utf-8")
    request = Request(
        discovery["token_endpoint"],
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_oidc_userinfo(access_token: str) -> dict:
    discovery = oidc_discovery()
    request = Request(
        discovery["userinfo_endpoint"],
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def user_from_oidc_claims(claims: dict):
    subject = claims.get("sub")
    if not subject:
        raise PermissionDenied("OIDC-Antwort enthält keine Subject-ID.")

    profile = UserProfile.objects.select_related("user").filter(
        oidc_subject=subject
    ).first()
    if profile is not None:
        user = profile.user
        _sync_user_from_oidc(user, profile, claims)
        return user

    user = _find_existing_user(claims)
    if user is None:
        if not settings.DOKSIO_OIDC_AUTO_CREATE_USERS:
            raise PermissionDenied("Benutzer darf nicht automatisch angelegt werden.")
        user = _create_user_from_oidc(claims)

    profile, _created = UserProfile.objects.get_or_create(user=user)
    _sync_user_from_oidc(user, profile, claims)
    return user


def tenant_slugs_from_oidc_claims(claims: dict) -> list[str]:
    raw_value = claims.get(settings.DOKSIO_OIDC_TENANT_CLAIM)
    if raw_value is None and settings.DOKSIO_OIDC_TENANT_CLAIM != "doksio_tenant":
        raw_value = claims.get("doksio_tenant")
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        candidates = raw_value.replace(",", " ").split()
    elif isinstance(raw_value, list | tuple | set):
        candidates = [str(value) for value in raw_value]
    else:
        candidates = [str(raw_value)]

    tenant_slugs = []
    for candidate in candidates:
        slug = candidate.strip().lower()
        if slug and slug not in tenant_slugs:
            tenant_slugs.append(slug)
    return tenant_slugs


def _find_existing_user(claims: dict):
    user_model = get_user_model()
    email = claims.get("email") or ""
    username = _username_from_claims(claims)
    if email:
        user = user_model.objects.filter(email__iexact=email).first()
        if user is not None:
            return user
    if username:
        return user_model.objects.filter(username=username).first()
    return None


def _create_user_from_oidc(claims: dict):
    user_model = get_user_model()
    username = _username_from_claims(claims)
    if not username:
        username = f"oidc-{claims['sub'][:32]}"
    base_username = username
    suffix = 1
    while user_model.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base_username}-{suffix}"
    user = user_model.objects.create_user(
        username=username,
        email=claims.get("email", ""),
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _sync_user_from_oidc(user, profile: UserProfile, claims: dict) -> None:
    user_update_fields = []
    email = claims.get("email") or ""
    if email and user.email != email:
        user.email = email
        user_update_fields.append("email")
    given_name = claims.get("given_name") or ""
    family_name = claims.get("family_name") or ""
    if given_name and user.first_name != given_name:
        user.first_name = given_name
        user_update_fields.append("first_name")
    if family_name and user.last_name != family_name:
        user.last_name = family_name
        user_update_fields.append("last_name")
    if user_update_fields:
        user.save(update_fields=user_update_fields)

    profile_update_fields = []
    subject = claims.get("sub")
    if subject and profile.oidc_subject != subject:
        profile.oidc_subject = subject
        profile_update_fields.append("oidc_subject")
    display_name = claims.get("name") or " ".join(
        part for part in [given_name, family_name] if part
    )
    if display_name and profile.display_name != display_name:
        profile.display_name = display_name
        profile_update_fields.append("display_name")
    if profile_update_fields:
        profile_update_fields.append("updated_at")
        profile.save(update_fields=profile_update_fields)


def _username_from_claims(claims: dict) -> str:
    username = claims.get(settings.DOKSIO_OIDC_USERNAME_CLAIM) or claims.get(
        "preferred_username"
    )
    if username:
        return username[:150]
    email = claims.get("email") or ""
    if email:
        return email.split("@", 1)[0][:150]
    return ""
