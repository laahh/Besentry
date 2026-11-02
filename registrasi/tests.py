from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import IntegrityError
from django.test import TestCase

from cctv.models import Cctv
from core.models import Perusahaan, Site

from .models import Registrasi

BOUNDARY = MultiPolygon(Polygon.from_bbox((117.27, 2.03, 117.28, 2.04)), srid=4326)


class RegistrasiTests(TestCase):
    databases = {'default', 'log'}

    def setUp(self):
        self.cctv = Cctv.objects.create(
            no_cctv='LMO-BC-0001', nama='CCTV 1',
            site=Site.objects.create(kode='LMO'), perusahaan=Perusahaan.objects.create(nama='PT BC'),
        )

    def test_one_running_request_per_cctv(self):
        Registrasi.objects.create(cctv=self.cctv)
        with self.assertRaises(IntegrityError):
            Registrasi.objects.create(cctv=self.cctv, tahap=Registrasi.Tahap.MENUNGGU_SGI)

    def test_finished_requests_do_not_block_a_new_one(self):
        Registrasi.objects.create(cctv=self.cctv, tahap=Registrasi.Tahap.DISETUJUI, boundary=BOUNDARY)
        Registrasi.objects.create(cctv=self.cctv, tahap=Registrasi.Tahap.DITOLAK)
        self.assertTrue(Registrasi.objects.create(cctv=self.cctv).berjalan)

    def test_boundary_required_before_safety(self):
        with self.assertRaises(IntegrityError):
            Registrasi.objects.create(cctv=self.cctv, tahap=Registrasi.Tahap.MENUNGGU_SAFETY)

    def test_sent_back_by_safety(self):
        registrasi = Registrasi(cctv=self.cctv, tahap=Registrasi.Tahap.MENUNGGU_SGI)
        self.assertFalse(registrasi.dikembalikan_safety)
        registrasi.catatan_safety = 'Boundary terlalu luas'
        self.assertTrue(registrasi.dikembalikan_safety)

    def test_history_is_kept(self):
        registrasi = Registrasi.objects.create(cctv=self.cctv)
        registrasi.tahap = Registrasi.Tahap.MENUNGGU_SGI
        registrasi.save()
        self.assertEqual([h.tahap for h in registrasi.history.all()], ['menunggu_sgi', 'draft_it'])
