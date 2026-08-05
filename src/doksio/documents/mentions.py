from __future__ import annotations

import re
from html import escape

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils.safestring import SafeString, mark_safe

from doksio.accounts.models import TenantRole

MENTION_RE = re.compile(r"(?<![\w.-])@([\w.-]+)")


def display_name_for_user(user) -> str:
    try:
        display_name = user.doksio_profile.display_name
    except ObjectDoesNotExist:
        display_name = ""

    display_name = display_name.strip()
    if display_name:
        return display_name

    full_name = user.get_full_name().strip()
    if full_name:
        return full_name

    return user.get_username()


def mentionable_users_for_tenant(tenant):
    return list(
        get_user_model()
        .objects.filter(
            tenant_memberships__tenant=tenant,
            tenant_memberships__is_active=True,
            is_active=True,
        )
        .select_related("doksio_profile")
        .distinct()
        .order_by("doksio_profile__display_name", "username")
    )


def mention_suggestions_for_tenant(tenant) -> list[dict]:
    user_suggestions = [
        {
            "username": user.get_username(),
            "display_name": display_name_for_user(user),
            "kind": "user",
        }
        for user in mentionable_users_for_tenant(tenant)
    ]
    role_suggestions = [
        {
            "username": f"gruppe.{role.slug}",
            "display_name": role.name,
            "kind": "role",
        }
        for role in TenantRole.objects.filter(
            tenant=tenant,
            is_active=True,
            is_public_group=True,
        ).order_by("name", "id")
    ]
    return [*role_suggestions, *user_suggestions]


def mentioned_entities_from_text(body: str, tenant) -> tuple[list, list[TenantRole]]:
    mention_keys = list(
        dict.fromkeys(match.group(1) for match in MENTION_RE.finditer(body))
    )
    if not mention_keys:
        return [], []

    usernames = [key for key in mention_keys if not key.startswith("gruppe.")]
    role_slugs = [key.removeprefix("gruppe.") for key in mention_keys if key.startswith("gruppe.")]
    users_by_username = {
        user.get_username(): user
        for user in mentionable_users_for_tenant(tenant)
        if user.get_username() in usernames
    }
    roles_by_slug = {
        role.slug: role
        for role in TenantRole.objects.filter(
            tenant=tenant,
            is_active=True,
            is_public_group=True,
            slug__in=role_slugs,
        )
    }
    return (
        [users_by_username[key] for key in usernames if key in users_by_username],
        [roles_by_slug[slug] for slug in role_slugs if slug in roles_by_slug],
    )


def mentioned_users_from_text(body: str, tenant) -> list:
    users, _roles = mentioned_entities_from_text(body, tenant)
    return users


def render_mentions(body: str, mentioned_users, mentioned_roles=()) -> SafeString:
    mentioned_usernames = {user.get_username() for user in mentioned_users}
    mentioned_role_keys = {f"gruppe.{role.slug}" for role in mentioned_roles}
    output = []
    position = 0

    for match in MENTION_RE.finditer(body):
        username = match.group(1)
        if username not in mentioned_usernames and username not in mentioned_role_keys:
            continue
        output.append(escape(body[position : match.start()]))
        output.append(
            '<span class="document-comment-mention">@'
            + escape(username)
            + "</span>"
        )
        position = match.end()

    output.append(escape(body[position:]))
    return mark_safe("".join(output).replace("\n", "<br>"))
