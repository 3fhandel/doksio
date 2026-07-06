# DoMaSy

DoMaSy is a slim, tenant-aware document management and workflow system for small
and medium-sized businesses.

The project is intentionally optimized for a clean modular architecture:
documents are immutable artifacts, workflows are generic, exports are
configurable, and business logic should live in explicit application services.

## Current Status

The repository is at the foundation stage. The architecture notes and ADRs live
in `docs/`.

## Planned Stack

- Python and Django
- PostgreSQL
- Celery with Redis
- S3-compatible object storage, with MinIO for local development
- Local OCR
- PostgreSQL full text search initially
- Django templates, HTMX, Alpine.js, Bootstrap and lucide SVG icons

OCR is local-only. The Docker image installs `ocrmypdf`, `pdftotext` and
German Tesseract language data; local non-Docker development needs equivalent
system packages for PDF/image OCR.

## Quick Local Smoke Test

The default local settings use SQLite so the project can be started without a
local PostgreSQL server:

```sh
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/s/health/
```

For the first document upload flow, create a demo tenant and an admin user:

```sh
.venv/bin/python manage.py bootstrap_demo_tenant
.venv/bin/python manage.py createsuperuser
```

Then start the server and open:

```text
http://127.0.0.1:8000/s/
```

System admins can access tenants without an explicit tenant membership. Normal
users need a `TenantMembership` with a tenant-scoped role. Tenant document URLs
include the tenant slug:

```text
http://127.0.0.1:8000/t/demo/documents/
```

The tenant-specific login page is:

```text
http://127.0.0.1:8000/t/demo/
```

Tenant document box settings are available for tenant admins and system admins:

```text
http://127.0.0.1:8000/t/demo/settings/document-boxes/
```

Tenant user and role settings are available at:

```text
http://127.0.0.1:8000/t/demo/settings/users/
http://127.0.0.1:8000/t/demo/settings/roles/
```

## Docker Development

Copy the example environment file and adjust values if needed:

```sh
cp .env.example .env
```

Install dependencies into an active virtual environment:

```sh
make install
```

Run checks and tests:

```sh
make check
make test
```

Start the Docker development stack with PostgreSQL, Redis and MinIO:

```sh
docker-compose up
```
