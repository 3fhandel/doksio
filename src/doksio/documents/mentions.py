from __future__ import annotations

import re
from html import escape

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils.safestring import SafeString, mark_safe

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
    return [
        {
            "username": user.get_username(),
            "display_name": display_name_for_user(user),
        }
        for user in mentionable_users_for_tenant(tenant)
    ]


def mentioned_users_from_text(body: str, tenant) -> list:
    usernames = list(
        dict.fromkeys(match.group(1) for match in MENTION_RE.finditer(body))
    )
    if not usernames:
        return []

    users_by_username = {
        user.get_username(): user
        for user in mentionable_users_for_tenant(tenant)
        if user.get_username() in usernames
    }
    return [
        users_by_username[username]
        for username in usernames
        if username in users_by_username
    ]


def render_mentions(body: str, mentioned_users) -> SafeString:
    mentioned_usernames = {user.get_username() for user in mentioned_users}
    output = []
    position = 0

    for match in MENTION_RE.finditer(body):
        username = match.group(1)
        if username not in mentioned_usernames:
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
