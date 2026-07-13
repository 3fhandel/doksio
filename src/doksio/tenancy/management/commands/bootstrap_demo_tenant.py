from __future__ import annotations

from django.core.management.base import BaseCommand

from doksio.tenancy.services import BootstrapDemoTenant


class Command(BaseCommand):
    help = "Create the default demo tenant for local development."

    def handle(self, *args, **options) -> None:
        tenant, created = BootstrapDemoTenant().execute()
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created tenant: {tenant.name}"))
        else:
            self.stdout.write(f"Tenant already exists: {tenant.name}")
