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

1. Build and push a Doksio image, for example `ghcr.io/example/doksio:latest`.
2. In Portainer, create a new Stack from `deploy/portainer-stack.yml`.
3. Copy values from `deploy/portainer.env.example` into Portainer environment
   variables and replace every secret.
4. Put a reverse proxy in front of `web` and point it at port `8000`.
5. Set `DOKSIO_PUBLIC_BASE_URL`, `DJANGO_ALLOWED_HOSTS` and
   `DJANGO_CSRF_TRUSTED_ORIGINS` to the real public URL.

## Notes

- Do not use `DJANGO_ALLOWED_HOSTS=*` in production.
- Keep PostgreSQL and MinIO volumes backed up.
- `web` and `worker` must use the same image tag.
- If `web` logs show missing application files, verify that `DOKSIO_IMAGE`
  points to the image built from this repository and that Portainer pulled the
  current tag.
- For existing data repairs or after manual imports, run:

```sh
python -m django rebuild_search_index
```
