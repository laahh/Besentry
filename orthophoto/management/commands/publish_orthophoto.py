from django.core.management.base import BaseCommand, CommandError

from orthophoto import services
from orthophoto.forms import parse_bulan
from orthophoto.models import Orthophoto


class Command(BaseCommand):
    help = 'Publish a raw orthophoto from the incoming folder right away, without the queue (for testing).'

    def add_arguments(self, parser):
        parser.add_argument('file', help='File name inside ORTHOPHOTO_INCOMING_DIR')
        parser.add_argument('--bulan', required=True, help='Month as YYYY-MM')

    def handle(self, *args, **options):
        try:
            bulan = parse_bulan(options['bulan'])
        except ValueError as exc:
            raise CommandError(exc) from exc

        ortho = Orthophoto.objects.create(bulan=bulan, file_sumber=options['file'])
        self.stdout.write(f'Publish #{ortho.pk} {ortho.bulan:%Y-%m} dari {ortho.file_sumber}')
        try:
            services.publish(ortho.pk, on_progress=lambda percent: self.stdout.write(f'  {percent}%'))
        except Exception as exc:
            raise CommandError(f'Gagal: {exc}') from exc

        ortho.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f'Selesai: {ortho.file_cog} aktif, zoom {ortho.zoom_min}-{ortho.zoom_maks}',
        ))
