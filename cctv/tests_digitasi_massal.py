import json
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from core.models import Karyawan, Perusahaan, Site
from navigation import roles
from notifikasi import channels, services
from notifikasi.models import Notifikasi, Pic

from .models import CakupanCctv, Cctv, PengajuanCoverage
from .tests_pengajuan import CAMERA, square

URL = '/cctv/tasklist/upload-shp/'


def shp_zip(features, srs='EPSG:32650', column='no_cctv'):
    """A zipped shapefile from [(CCTV number, WGS 84 polygon)]."""
    folder = Path(tempfile.mkdtemp())
    try:
        collection = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {column: number}, 'geometry': json.loads(polygon.geojson)}
            for number, polygon in features
        ]}
        (folder / 'in.geojson').write_text(json.dumps(collection))
        subprocess.run(['ogr2ogr', '-t_srs', srs, '-f', 'ESRI Shapefile', str(folder / 'batch.shp'),
                        str(folder / 'in.geojson')], check=True, capture_output=True)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for ext in ('.shp', '.shx', '.dbf', '.prj'):
                archive.write(folder / f'batch{ext}', f'batch{ext}')
        return SimpleUploadedFile('batch.zip', buffer.getvalue(), content_type='application/zip')
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@override_settings(FONNTE_TOKEN='test-token', SITE_URL='https://besentry.example')
class DigitasiMassalTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal')
        cls.mitra = get_user_model().objects.create_user('mitra', password='x')
        cls.mitra.groups.add(Group.objects.get_or_create(name=roles.SURVEY_MITRA)[0])
        role = Group.objects.create(name='CCTV LMO')
        CakupanCctv.objects.create(group=role, site=cls.lmo, perusahaan=cls.perusahaan)
        cls.mitra.groups.add(role)
        cls.satu = cls._request('LMO-1', cls.lmo)
        cls.dua = cls._request('LMO-2', cls.lmo, status='return_mitra')
        cls.smo_task = cls._request('SMO-1', cls.smo)

    @classmethod
    def _request(cls, number, site, status='menunggu_mitra'):
        cctv = Cctv.objects.create(no_cctv=number, nama='Kamera', site=site, perusahaan=cls.perusahaan,
                                   posisi=Point(*CAMERA, srid=4326))
        return PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status=status)

    def setUp(self):
        self.client.force_login(self.mitra)
        patcher = mock.patch.object(channels, 'send_wa')
        self.send_wa = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, upload, aksi):
        return self.client.post(URL, {'file_shp': upload, 'aksi': aksi})

    def good(self, *numbers):
        return [(number, square(*CAMERA)) for number in numbers]

    def test_check_reports_each_feature_without_saving(self):
        far = square(CAMERA[0] + 0.05, CAMERA[1])
        upload = shp_zip([*self.good('lmo-1'), ('LMO-2', far), *self.good('SMO-1', 'ZZZ-9', 'ZZZ-8', 'ZZZ-8', '')])
        response = self.post(upload, 'cek')
        self.assertEqual(response.status_code, 200)
        by_number = {}
        for row in response.context['rows']:
            by_number.setdefault(row['no_cctv'], row)
        self.assertTrue(by_number['lmo-1']['valid'])
        self.assertIn('melebihi batas', by_number['LMO-2']['pesan'])
        # Out of this user's scope reads the same as a number that does not exist.
        self.assertEqual(by_number['SMO-1']['pesan'], by_number['ZZZ-9']['pesan'])
        self.assertIn('muncul 2 kali', by_number['ZZZ-8']['pesan'])
        self.assertIn('kosong', by_number['(kosong)']['pesan'])
        self.assertEqual(response.context['jumlah_lolos'], 1)
        self.assertFalse(response.context['rows'][0]['valid'])  # problems first
        self.assertContains(response, 'data-lolos="1"')
        self.satu.refresh_from_db()
        self.assertEqual(self.satu.status, 'menunggu_mitra')
        self.assertIsNone(self.satu.geom)

    def test_check_lists_tasks_missing_from_the_file(self):
        response = self.post(shp_zip(self.good('LMO-1')), 'cek')
        self.assertEqual(response.context['uncovered'], ['LMO-2'])

    def test_send_moves_what_passes_and_leaves_the_rest(self):
        far = square(CAMERA[0] + 0.05, CAMERA[1])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post(shp_zip([*self.good('LMO-1'), ('LMO-2', far), *self.good('SMO-1')]), 'kirim')
        self.assertRedirects(response, '/cctv/tasklist/', fetch_redirect_response=False)
        self.satu.refresh_from_db()
        self.dua.refresh_from_db()
        self.smo_task.refresh_from_db()
        self.assertEqual(self.satu.status, 'menunggu_sgi')
        self.assertEqual((self.satu.sumber_geom, self.satu.file_shp, self.satu.digitasi_oleh),
                         ('shp', 'batch.zip', self.mitra))
        self.assertIsNotNone(self.satu.geom)
        self.assertEqual(set(self.satu.riwayat.values_list('aksi', flat=True)), {'digitasi', 'validasi'})
        self.assertEqual(self.dua.status, 'return_mitra')
        self.assertEqual(self.smo_task.status, 'menunggu_mitra')

    def test_berau_coal_digitised_by_survey_bc_is_not_a_bulk_task(self):
        berau_coal = Perusahaan.objects.create(nama='PT Berau Coal BC', kode=Perusahaan.KODE_BERAU_COAL)
        cctv = Cctv.objects.create(no_cctv='BC-1', nama='Kamera', site=self.lmo, perusahaan=berau_coal,
                                   posisi=Point(*CAMERA, srid=4326))
        PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status='menunggu_sgi')
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get_or_create(name=roles.ADMIN_HAZARD_MOTION)[0])
        self.client.force_login(admin)
        response = self.post(shp_zip(self.good('BC-1')), 'cek')
        self.assertEqual(response.context['jumlah_lolos'], 0)
        self.assertEqual(response.context['rows'][0]['pesan'], 'Tidak ada tugas digitasi untuk CCTV ini.')

    def test_sending_twice_does_not_repeat(self):
        self.post(shp_zip(self.good('LMO-1')), 'kirim')
        self.post(shp_zip(self.good('LMO-1')), 'kirim')
        self.assertEqual(self.satu.riwayat.filter(aksi='digitasi').count(), 1)

    def test_survey_bc_gets_one_message_for_the_whole_upload(self):
        Pic.objects.create(nama='SGI', tahap='sgi', no_wa='6284444444444', email='sgi@example.com')
        with self.captureOnCommitCallbacks(execute=True):
            self.post(shp_zip(self.good('LMO-1', 'LMO-2')), 'kirim')
        self.assertEqual(Notifikasi.objects.count(), 2)
        self.send_wa.assert_called_once()
        pesan = self.send_wa.call_args[0][1]
        self.assertIn('*Waiting Survey BC: 2 CCTV*', pesan)
        self.assertIn('- LMO-1', pesan)
        self.assertIn('- LMO-2', pesan)
        self.assertIn('https://besentry.example/cctv/tasklist/', pesan)
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] Waiting Survey BC: 2 CCTV')

    def test_long_message_names_only_the_first_cctv(self):
        numbers = [f'N-{index}' for index in range(services.BATCH_LISTED + 5)]
        pesan = services._batch_message(self.satu, numbers, self.mitra)
        self.assertIn(f'- N-{services.BATCH_LISTED - 1}\n', pesan)
        self.assertNotIn(f'- N-{services.BATCH_LISTED}\n', pesan)
        self.assertIn('dan 5 CCTV lainnya', pesan)

    def test_whole_file_problems_are_reported(self):
        cases = (
            (shp_zip(self.good('LMO-1'), srs='EPSG:4326'), 'CRS shapefile'),
            (shp_zip(self.good('LMO-1'), column='nomor'), 'kolom no_cctv'),
            (SimpleUploadedFile('x.zip', b'bukan zip'), 'harus berupa .zip'),
        )
        for upload, message in cases:
            with self.subTest(message):
                response = self.post(upload, 'cek')
                self.assertContains(response, message, status_code=400)

    def test_missing_file_is_reported(self):
        response = self.client.post(URL, {'aksi': 'cek'})
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, 'data-lolos', status_code=400)

    def test_page_counts_the_tasks_of_this_user(self):
        self.assertEqual(self.client.get(URL).context['jumlah_tugas'], 2)

    def test_assigned_mitra_pic_may_use_it_within_their_coverage(self):
        user = get_user_model().objects.create_user('budi', password='x')
        karyawan = Karyawan.objects.create(sid='BUDI1', nama='BUDI', user=user)
        Pic.objects.create(karyawan=karyawan, nama='BUDI', tahap='mitra', email='b@example.com', site=self.smo)
        self.client.force_login(user)
        self.assertEqual(self.client.get(URL).context['jumlah_tugas'], 1)

    def test_other_stages_cannot_use_it(self):
        user = get_user_model().objects.create_user('sgi', password='x')
        user.groups.add(Group.objects.get_or_create(name=roles.SURVEY_BC)[0])
        self.client.force_login(user)
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)
