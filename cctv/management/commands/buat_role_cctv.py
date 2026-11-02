from django.core.management.base import BaseCommand

from cctv.access import create_pair_roles


class Command(BaseCommand):
    help = ('Buat satu role per pasangan site + perusahaan yang punya CCTV, mis. '
            '"CCTV BMO 2 - PT Pamapersada Nusantara". Role yang sudah ada tidak diubah.')

    def add_arguments(self, parser):
        parser.add_argument('--bisa-ubah', action='store_true', help='Role baru boleh tambah/ubah CCTV.')

    def handle(self, bisa_ubah, **options):
        created, pairs = create_pair_roles(bisa_ubah=bisa_ubah)
        for name in created:
            self.stdout.write(f'  + {name}')
        self.stdout.write(self.style.SUCCESS(
            f'{len(created)} role baru, {pairs - len(created)} sudah ada. '
            'Masukkan user ke role di Pengaturan > Pengguna.'
        ))
