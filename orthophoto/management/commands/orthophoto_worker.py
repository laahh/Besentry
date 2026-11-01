from django.core.management import call_command
from django.core.management.base import BaseCommand

from orthophoto import jobs


class Command(BaseCommand):
    help = 'Clean up interrupted orthophoto publishes, then run the orthophoto queue worker.'

    def handle(self, *args, **options):
        failed = jobs.fail_interrupted()
        if failed:
            self.stdout.write(self.style.WARNING(f'{failed} publish yang terputus ditandai gagal.'))
        call_command('rqworker', jobs.QUEUE)
