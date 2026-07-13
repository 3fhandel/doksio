from __future__ import annotations

from django.db import models


class Tenant(models.Model):
    """A tenant boundary for all business data."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
