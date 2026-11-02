from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import karyawan_sync, sid_api
from core.models import Perusahaan


class Command(BaseCommand):
    help = ('Sinkron perusahaan dan karyawan dari API SID. Default: semua perusahaan SID, lalu karyawan '
            'dari perusahaan yang dipakai di BeSentry (punya CCTV atau karyawan yang pernah login). '
            'Karyawan yang tidak lagi AKTIF tidak bisa login.')

    def add_arguments(self, parser):
        parser.add_argument('--perusahaan', type=int, action='append', default=[],
                            help='companyId SID tertentu (boleh berulang).')
        parser.add_argument('--semua', action='store_true',
                            help='Karyawan dari semua perusahaan SID (bisa puluhan ribu baris).')
        parser.add_argument('--tanpa-perusahaan', action='store_true', help='Lewati sinkron daftar perusahaan.')

    def handle(self, perusahaan, semua, tanpa_perusahaan, **options):
        if not settings.SID_API_KEY:
            raise CommandError('SID_API_KEY belum diisi di .env.')
        try:
            if not tanpa_perusahaan:
                self.stdout.write(f'Perusahaan: {karyawan_sync.sync_companies()} dari SID.')

            targets = Perusahaan.objects.filter(sid_id__isnull=False)
            if perusahaan:
                targets = targets.filter(sid_id__in=perusahaan)
            elif not semua:
                targets = targets.filter(cctv__isnull=False) | targets.filter(karyawan__user__isnull=False)
            targets = targets.distinct().order_by('nama')

            total = 0
            for target in targets:
                report = karyawan_sync.sync_employees(target)
                total += report.karyawan
                line = f'  {target.nama}: {report.karyawan} karyawan'
                if report.dinonaktifkan:
                    line += f', login dinonaktifkan: {", ".join(report.dinonaktifkan)}'
                self.stdout.write(line)
        except sid_api.ApiUnavailable as exc:
            raise CommandError(f'API SID tidak bisa diakses: {exc}') from exc
        self.stdout.write(self.style.SUCCESS(f'Selesai: {total} karyawan dari {len(targets)} perusahaan.'))
