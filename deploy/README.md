# Portainer Deployment

Use `portainer-stack.yml` as a Portainer Stack template for a single-host
Doksio deployment.

## Services

- `web`: Django via Gunicorn, runs migrations and `collectstatic` on startup
- `worker`: Celery worker for OCR/import jobs
- `db`: PostgreSQL
- `redis`: Celery broker
- `minio`: S3-compatible object storage for immutable document files

## Deployment Steps

1. In Portainer, create a new Stack from the Git repository.
2. Use `deploy/portainer-stack.yml` as the compose path.
3. Copy values from `deploy/portainer.env.example` into Portainer environment
   variables and replace every secret.
4. Leave `DOKSIO_IMAGE` unset unless you intentionally deploy a prebuilt image
   from a registry.
5. Put a reverse proxy in front of `web` and point it at port `8000`.
6. Set `DOKSIO_PUBLIC_BASE_URL`, `DJANGO_ALLOWED_HOSTS` and
   `DJANGO_CSRF_TRUSTED_ORIGINS` to the real public URL.

## Notes

- Do not use `DJANGO_ALLOWED_HOSTS=*` in production.
- Keep PostgreSQL and MinIO volumes backed up.
- The default stack builds the Doksio image directly from the Git repository.
- If `web` logs show missing application files, verify that the Stack was
  deployed from the repository root and that the compose path is exactly
  `deploy/portainer-stack.yml`.
- For existing data repairs or after manual imports, run:

```sh
python -m django rebuild_search_index
```
