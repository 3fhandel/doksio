# Doksio

Doksio is a slim, tenant-aware document management and workflow system for small
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
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

Open:

```text
http://127.0.0.1:8000/s/health/
```

For access from another device in the local network, open the same URL with the
development machine's LAN IP, for example:

```text
http://192.168.178.42:8000/s/health/
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

Set `DOKSIO_PUBLIC_BASE_URL` in `.env` to the externally reachable system URL.
Doksio uses this value for generated API URLs, import scripts and later
notification links. For LAN testing this can be, for example:

```text
DOKSIO_PUBLIC_BASE_URL=http://192.168.178.42:8000
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

Rebuild the denormalized document search index after imports or data repairs:

```sh
.venv/bin/python manage.py rebuild_search_index --tenant demo
```

Generate local synthetic load data for performance checks:

```sh
.venv/bin/python manage.py generate_performance_documents --tenant demo --count 50000
```

Run a small search benchmark against those documents:

```sh
.venv/bin/python manage.py benchmark_search lasttest --tenant demo --explain
```

Start the Docker development stack with PostgreSQL, Redis and MinIO:

```sh
docker-compose up
```

## Portainer Deployment

A production-oriented single-host Portainer stack lives in `deploy/`.

Use:

```text
deploy/portainer-stack.yml
deploy/portainer.env.example
```

Build and push one Doksio image, set `DOKSIO_IMAGE` in Portainer, then deploy
the stack behind a reverse proxy. The Portainer stack runs Django via Gunicorn,
uses PostgreSQL/Redis/MinIO services, creates the MinIO bucket automatically and
serves static files through WhiteNoise.
