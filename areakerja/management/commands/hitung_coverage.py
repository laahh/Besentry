"""Recompute this week's coverage of the work areas, e.g. after loading data with SQL (which bypasses the
signals that normally do it).

    python manage.py hitung_coverage
"""

from django.core.management.base import BaseCommand

from areakerja import kalkulasi


class Command(BaseCommand):
    help = 'Hitung ulang coverage CCTV week berjalan untuk semua Mitra Site aktif.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--snapshot', action='store_true',
            help='Hitung ulang juga seluruh snapshot historis untuk varian dengan dan tanpa PTZ.',
        )

    def handle(self, snapshot=False, **options):
        done = kalkulasi.hitung_live()
        self.stdout.write(self.style.SUCCESS(f'Coverage week berjalan dihitung untuk {done} Mitra Site.'))
        if snapshot:
            locked = kalkulasi.hitung_ulang_snapshot()
            self.stdout.write(self.style.SUCCESS(f'{locked} snapshot historis dihitung ulang.'))
