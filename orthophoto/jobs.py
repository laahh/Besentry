"""Queue glue: the web app enqueues, the `worker` service runs `publish_job`."""

import django_rq
from django.utils import timezone
from rq.job import Job

from . import services
from .models import Orthophoto

QUEUE = 'orthophoto'
INTERRUPTED_MESSAGE = 'Worker berhenti sebelum proses selesai. Silakan publish ulang.'


def publish_job(ortho_id):
    services.publish(ortho_id)


def enqueue_publish(ortho):
    job = django_rq.get_queue(QUEUE).enqueue(publish_job, ortho.pk, job_id=f'orthophoto-{ortho.pk}')
    Orthophoto.objects.filter(pk=ortho.pk).update(job_id=job.id)


def fail_interrupted():
    """Mark publishes a stopped worker left behind as failed. Run before the worker starts."""
    connection = django_rq.get_connection(QUEUE)
    stale = [
        ortho.pk for ortho in Orthophoto.objects.filter(status__in=Orthophoto.BERJALAN)
        # With one worker, anything "diproses" at startup died with the previous worker.
        if ortho.status == Orthophoto.Status.DIPROSES
        or not (ortho.job_id and Job.exists(ortho.job_id, connection=connection))
    ]
    return Orthophoto.objects.filter(pk__in=stale).update(
        status=Orthophoto.Status.GAGAL, pesan_error=INTERRUPTED_MESSAGE, selesai=timezone.now(),
    )
