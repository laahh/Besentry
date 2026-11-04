import datetime
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from cctv.models import minggu_label
from cctv.snapshot import close_week, last_closed_week


def _next_monday_midnight():
    now = timezone.localtime()
    days = 7 - now.weekday()
    return (now + datetime.timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


class Command(BaseCommand):
    help = ('Tutup minggu lalu: simpan snapshot semua CCTV, boundary, dan status pengajuan (WITA), lalu '
            'snapshot coverage area kerja per Mitra Site. Minggu yang sudah ditutup tidak diubah lagi.')

    def add_arguments(self, parser):
        parser.add_argument('--tunggu', action='store_true',
                            help='Jalan terus: tutup minggu setiap Senin 00.00 WITA (service "snapshot").')

    def handle(self, tunggu, **options):
        self._close()  # also catches up a week missed while the server was down
        while tunggu:
            wait = (_next_monday_midnight() - timezone.localtime()).total_seconds()
            self.stdout.write(f'Snapshot berikutnya {_next_monday_midnight():%d %b %Y %H:%M} WITA.')
            time.sleep(max(wait, 0) + 5)
            self._close()

    def _close(self):
        minggu = last_closed_week()
        written = close_week(minggu)
        if written:
            self.stdout.write(self.style.SUCCESS(f'{minggu_label(minggu)} ditutup: {written} CCTV.'))
        else:
            self.stdout.write(f'{minggu_label(minggu)} sudah ditutup sebelumnya.')
        from areakerja import kalkulasi  # after the CCTV snapshot, which it is computed against
        locked, waiting = kalkulasi.tutup_minggu(minggu)
        late = kalkulasi.selesaikan_menunggu()
        if locked or waiting or late:
            self.stdout.write(self.style.SUCCESS(
                f'Coverage {minggu_label(minggu)}: {locked} Mitra Site terkunci, {waiting} menunggu area kerja; '
                f'{late} week tertunda diselesaikan.'))
        kalkulasi.hitung_live()  # the new week starts
