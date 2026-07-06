from __future__ import annotations

from celery import shared_task

from domasy.ocr.models import OcrJob
from domasy.ocr.services import RunOcrJob


@shared_task
def run_ocr_job(job_id: int) -> int:
    job = OcrJob.objects.select_related("document_file", "tenant").get(id=job_id)
    RunOcrJob(job=job).execute()
    return job_id
