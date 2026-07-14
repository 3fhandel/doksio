# Portainer Deployment

Use `portainer-stack.yml` as a Portainer Stack template for a single-host
Doksio deployment.

## Services

- `web`: Django via Gunicorn, runs migrations and `collectstatic` on startup
- `worker`: Celery worker for OCR/import jobs
- `beat`: Celery Beat scheduler for periodic import polling
- `db`: PostgreSQL
- `redis`: Celery broker
- `minio`: S3-compatible object storage for immutable document files

## Deployment Steps

1. In Portainer, create a new Stack from the Git repository.
2. Use `deploy/portainer-stack.yml` as the compose path.
3. Copy values from `deploy/portainer.env.example` into Portainer environment
   variables and replace every secret.
4. Put a reverse proxy in front of `web` and point it at port `8000`.
5. Set `DOKSIO_PUBLIC_BASE_URL`, `DJANGO_ALLOWED_HOSTS` and
   `DJANGO_CSRF_TRUSTED_ORIGINS` to the real public URL.
6. Deploy or update the stack. The Docker build writes the last Git commit
   timestamp into the image automatically, so the top bar shows the deployed
   build without an extra Portainer variable.

## Notes

- Do not use `DJANGO_ALLOWED_HOSTS=*` in production.
- Keep PostgreSQL and MinIO volumes backed up.
- OCR is CPU-heavy. Start with `CELERY_WORKER_CONCURRENCY=1`,
  `CELERY_WORKER_PREFETCH_MULTIPLIER=1` and `OMP_THREAD_LIMIT=1`; raise these
  only after watching CPU load during larger imports.
- The default stack builds the Doksio image directly from the Git repository.
- If `web` logs show missing application files, verify that the Stack was
  deployed from the repository root and that the compose path is exactly
  `deploy/portainer-stack.yml`.
- For existing data repairs or after manual imports, run:

```sh
python -m django rebuild_search_index
```

## Authentik / OIDC

Doksio can use authentik as an OpenID Connect identity provider while keeping
tenant roles and permissions in Doksio.

Configure an authentik OAuth2/OpenID provider with this redirect URI:

```text
https://doksio.example.test/s/oidc/callback/
```

Then set these stack variables:

```env
DOKSIO_OIDC_ENABLED=true
DOKSIO_OIDC_ISSUER_URL=https://auth.example.test/application/o/doksio
DOKSIO_OIDC_CLIENT_ID=...
DOKSIO_OIDC_CLIENT_SECRET=...
DOKSIO_OIDC_TENANT_CLAIM=doksio_tenants
```

Users are matched by stored OIDC subject, then initially by email or username.
New users can be created automatically, but they still need a Doksio tenant
membership before they can access `/t/<tenant>/...`.

For tenant auto-detection, add a claim in authentik that contains one Doksio
tenant slug, or a list of slugs:

```json
{
  "doksio_tenants": ["demo"]
}
```

The generic tenant login URL is `/s/oidc/tenant-login/`. Doksio only uses the
claim as a hint and still checks the local tenant membership before logging the
user into `/t/<tenant>/...`.
