"""Queue glue: the web app enqueues, the `worker` service runs `publish_job`."""

import datetime

import django_rq
from django.utils import timezone
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from . import services
from .models import Orthophoto

QUEUE = 'orthophoto'
INTERRUPTED_MESSAGE = 'Worker berhenti sebelum proses selesai. Silakan publish ulang.'
MISSING_JOB_MESSAGE = 'Job tidak ditemukan di antrean. Silakan publish ulang.'
DEAD_STATUSES = (JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED)
# enqueue_publish runs right after the record is committed; give it time before calling a job missing.
ENQUEUE_GRACE = datetime.timedelta(minutes=1)


def publish_job(ortho_id):
    services.publish(ortho_id)


def enqueue_publish(ortho):
    job = django_rq.get_queue(QUEUE).enqueue(publish_job, ortho.pk, job_id=f'orthophoto-{ortho.pk}')
    Orthophoto.objects.filter(pk=ortho.pk).update(job_id=job.id)


def _fetch_job(ortho, connection):
    if not ortho.job_id:
        return None
    try:
        return Job.fetch(ortho.job_id, connection=connection)
    except NoSuchJobError:
        return None


def _failure_message(job):
    """The last line of the job's traceback, e.g. "PermissionError: [Errno 13] ..."."""
    result = job.latest_result()
    lines = (result.exc_string or '').strip().splitlines() if result else []
    return lines[-1] if lines else f'Job {job.get_status()} di antrean.'


def _mark_failed(pks_messages):
    now = timezone.now()
    for pk, message in pks_messages:
        Orthophoto.objects.filter(pk=pk, status__in=Orthophoto.BERJALAN).update(
            status=Orthophoto.Status.GAGAL, pesan_error=message, selesai=now,
        )
    return len(pks_messages)


def sync_with_queue():
    """Mark running publishes whose rq job failed or vanished as failed, with rq's error.

    A job can die before publish() records anything (e.g. it cannot even start),
    which would otherwise leave the record on "menunggu" and block new publishes.
    """
    connection = django_rq.get_connection(QUEUE)
    failed = []
    for ortho in Orthophoto.objects.filter(status__in=Orthophoto.BERJALAN):
        job = _fetch_job(ortho, connection)
        if job is None:
            if timezone.now() - ortho.dibuat > ENQUEUE_GRACE:
                failed.append((ortho.pk, MISSING_JOB_MESSAGE))
        elif job.get_status() in DEAD_STATUSES:
            failed.append((ortho.pk, _failure_message(job)))
    return _mark_failed(failed)


def fail_interrupted():
    """Run before the worker starts: whatever it was processing died with the previous worker."""
    stopped = Orthophoto.objects.filter(status=Orthophoto.Status.DIPROSES).values_list('pk', flat=True)
    return _mark_failed([(pk, INTERRUPTED_MESSAGE) for pk in stopped]) + sync_with_queue()
