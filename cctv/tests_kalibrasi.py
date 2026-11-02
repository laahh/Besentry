"""Safety calibrates the camera from the CCTV screenshot; the coverage is checked with OpenCV."""

import json
import shutil
import tempfile

import cv2
import numpy as np
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Perusahaan, Site
from navigation import roles

from . import coverage
from .models import CakupanCctv, Cctv, CctvSpec, KalibrasiKamera, PengajuanCoverage
from .tests_homography import CAMERA_LONLAT, GROUND, H, W, box, gcp

SCREENSHOT = cv2.imencode('.png', np.full((H, W, 3), 90, np.uint8))[1].tobytes()


class KalibrasiTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        site, perusahaan = Site.objects.create(kode='LMO'), Perusahaan.objects.create(nama='PT Berau Coal')
        cls.cctv = Cctv.objects.create(no_cctv='LMO-BC-0001', nama='Pit J', site=site, perusahaan=perusahaan,
                                       posisi=Point(*CAMERA_LONLAT, srid=4326), lokasi_pemasangan='Bench 1')
        CctvSpec.objects.create(cctv=cls.cctv, radius_m=100)  # reach = 150 m
        it_role = Group.objects.create(name='CCTV LMO')
        CakupanCctv.objects.create(group=it_role, site=site, perusahaan=perusahaan, bisa_ubah=True)
        cls.it = User.objects.create_user('it', password='x')
        cls.it.groups.add(it_role)
        cls.safety = User.objects.create_user('safety', password='x')
        cls.safety.groups.add(Group.objects.get(name=roles.SAFETY))

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.pengajuan = PengajuanCoverage(cctv=self.cctv, jenis='dipindah', versi=1,
                                           status=PengajuanCoverage.Status.MENUNGGU_SAFETY,
                                           geom=box(-10, 50, 10, 80))
        self.pengajuan.tangkapan_layar.save('layar.png', ContentFile(SCREENSHOT))
        self.client.force_login(self.safety)

    def save(self, gcps, lebar=W, tinggi=H):
        return self.client.post(reverse('cctv:coverage_kalibrasi', args=[self.pengajuan.pk]),
                                data=json.dumps({'gcp': gcps, 'lebar': lebar, 'tinggi': tinggi}),
                                content_type='application/json')

    def test_safety_calibrates_and_gets_the_analysis_and_evidence(self):
        response = self.save([gcp(*p) for p in GROUND])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLess(response.json()['hasil']['rmse_m'], 0.1)

        kalibrasi = KalibrasiKamera.objects.get(aktif=True)
        self.assertEqual((kalibrasi.cctv, len(kalibrasi.gcp)), (self.cctv, 6))
        self.pengajuan.refresh_from_db()
        self.assertGreater(self.pengajuan.analisis['persen_terlihat'], 99)
        self.assertEqual(self.pengajuan.analisis['jangkauan_m'], 150)
        self.assertTrue(self.pengajuan.bukti.name.endswith('.jpg'))
        self.assertTrue(self.pengajuan.riwayat.filter(aksi='kalibrasi').exists())

        page = self.client.get(reverse('cctv:coverage_index'), {'status': 'menunggu_safety', 'id': self.pengajuan.pk})
        self.assertContains(page, 'coverage terlihat kamera')
        self.assertEqual(page.context['peta']['kalibrasi']['lebar'], W)
        evidence = self.client.get(reverse('cctv:coverage_bukti', args=[self.pengajuan.pk]))
        self.assertTrue(b''.join(evidence.streaming_content).startswith(b'\xff\xd8'))

    def test_misclicked_gcp_is_reported(self):
        self.save([gcp(*p) for p in GROUND] + [gcp(-10, 55, dx=25)])
        self.pengajuan.refresh_from_db()
        self.assertEqual(self.pengajuan.analisis['gcp_outlier'], [7])

    def test_recalibrating_replaces_the_cameras_calibration(self):
        self.save([gcp(*p) for p in GROUND[:4]])
        self.save([gcp(*p) for p in GROUND])
        self.assertEqual(KalibrasiKamera.objects.count(), 2)
        self.assertEqual(KalibrasiKamera.objects.get(aktif=True).gcp.__len__(), 6)

    def test_bad_calibrations_are_refused(self):
        good = [gcp(*p) for p in GROUND[:4]]
        self.assertIn('Butuh 4', self.save(good[:3]).json()['detail'])
        self.assertIn('di luar gambar', self.save([{**good[0], 'px': W + 100}, *good[1:]]).json()['detail'])
        self.assertIn('wilayah', self.save([{**good[0], 'lon': 110.0}, *good[1:]]).json()['detail'])
        line = [gcp(0, y) for y in (30, 50, 70, 90)]
        self.assertIn('tidak membentuk kamera', self.save(line).json()['detail'])
        self.assertFalse(KalibrasiKamera.objects.exists())

    def test_only_safety_or_admin_calibrates_and_not_after_approval(self):
        self.client.force_login(self.it)
        self.assertEqual(self.save([gcp(*p) for p in GROUND]).status_code, 403)
        PengajuanCoverage.objects.filter(pk=self.pengajuan.pk).update(status='selesai')
        self.client.force_login(self.safety)
        self.assertEqual(self.save([gcp(*p) for p in GROUND]).status_code, 403)

    def test_calibration_is_reused_and_redone_when_the_polygon_changes(self):
        self.save([gcp(*p) for p in GROUND])
        first = self.pengajuan.__class__.objects.get(pk=self.pengajuan.pk).analisis['persen_terlihat']
        # SGI redraws a much wider polygon: the next page view analyses it again with the same calibration.
        PengajuanCoverage.objects.filter(pk=self.pengajuan.pk).update(geom=box(-100, 45, 100, 55))
        self.client.get(reverse('cctv:coverage_index'), {'status': 'menunggu_safety', 'id': self.pengajuan.pk})
        again = PengajuanCoverage.objects.get(pk=self.pengajuan.pk).analisis['persen_terlihat']
        self.assertGreater(first, 99)
        self.assertLess(again, 50)

    def test_moving_the_cctv_switches_its_calibration_off(self):
        self.save([gcp(*p) for p in GROUND])
        PengajuanCoverage.objects.filter(pk=self.pengajuan.pk).update(status='selesai')
        coverage.create_digitasi_task(self.cctv, self.it, PengajuanCoverage.Jenis.DIPINDAH, 'dipindah')
        self.assertFalse(KalibrasiKamera.objects.filter(aktif=True).exists())

    def test_upload_screenshot(self):
        url = reverse('cctv:coverage_upload', args=[self.pengajuan.pk])
        self.client.post(url, {'tangkapan_layar': SimpleUploadedFile('x.txt', b'bukan gambar')})
        self.pengajuan.refresh_from_db()
        self.assertTrue(self.pengajuan.tangkapan_layar.name.endswith('layar.png'))

        self.client.post(url, {'tangkapan_layar': SimpleUploadedFile('baru.png', SCREENSHOT)})
        self.pengajuan.refresh_from_db()
        self.assertIn('baru', self.pengajuan.tangkapan_layar.name)
