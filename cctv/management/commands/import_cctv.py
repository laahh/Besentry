from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook

from cctv.importer import import_workbook

WARNINGS_SHOWN = 50


class Command(BaseCommand):
    help = ('Import CCTV dari spreadsheet (.xlsx, Google Sheets > File > Download > Microsoft Excel). '
            'No. CCTV yang sudah ada diperbarui.')

    def add_arguments(self, parser):
        parser.add_argument('file', type=Path)
        parser.add_argument('--sheet', action='append', default=[], help='Hanya tab ini (boleh berulang).')

    def handle(self, file, sheet, **options):
        if not file.is_file():
            raise CommandError(f'File tidak ditemukan: {file}')
        workbook = load_workbook(file, read_only=True, data_only=True)
        try:
            report, imported = import_workbook(workbook, only=set(sheet))
        finally:
            workbook.close()

        if not imported:
            raise CommandError('Tidak ada tab CCTV yang cocok dengan template.')
        self.stdout.write(f'Tab: {", ".join(imported)}')
        for warning in report.warnings[:WARNINGS_SHOWN]:
            self.stdout.write(self.style.WARNING(f'  {warning}'))
        if len(report.warnings) > WARNINGS_SHOWN:
            self.stdout.write(self.style.WARNING(f'  ... dan {len(report.warnings) - WARNINGS_SHOWN} peringatan lain'))
        self.stdout.write(self.style.SUCCESS(
            f'Baru {report.created}, diperbarui {report.updated}, dilewati {report.skipped} '
            f'(baris tanpa No. CCTV/site/perusahaan), peringatan {len(report.warnings)}.'
        ))
