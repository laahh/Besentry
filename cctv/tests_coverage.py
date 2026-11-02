import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Perusahaan, Site
from navigation import roles

from . import coverage
from .models import CakupanCctv, Cctv, CctvCoverage, CctvSpec, PengajuanCoverage

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64
CAMERA = (117.60, 2.20)


def square(x, y, size=0.001):
    """A small square near (x, y): ~110 m per 0.001 degree here."""
    return Polygon(((x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)), srid=4326)


def geojson(polygon):
    return json.dumps({'type': 'Polygon', 'coordinates': [list(map(list, polygon.coords[0]))]})


class CoverageTestMixin:
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.site, cls.other_site = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal', kode='BC')
        cls.cctv = Cctv.objects.create(no_cctv='LMO-BC-0014', nama='Pit J', site=cls.site,
                                       perusahaan=cls.perusahaan, posisi=Point(*CAMERA, srid=4326))
        CctvSpec.objects.create(cctv=cls.cctv, radius_m=300)
        cls.other = Cctv.objects.create(no_cctv='SMO-BC-0019', nama='Disposal', site=cls.other_site,
                                        perusahaan=cls.perusahaan, posisi=Point(117.41, 2.16, srid=4326))

        mitra_role = Group.objects.create(name='CCTV LMO - PT Berau Coal')
        CakupanCctv.objects.create(group=mitra_role, site=cls.site, perusahaan=cls.perusahaan, bisa_ubah=True)
        cls.mitra = User.objects.create_user('mitra', password='x')
        cls.mitra.groups.add(mitra_role)
        cls.reviewer = User.objects.create_user('sgi', password='x')
        cls.reviewer.groups.add(Group.objects.get(name=roles.ADMIN_HAZARD_MOTION))

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def post_request(self, **extra):
        data = {'no_cctv': self.cctv.no_cctv, 'jenis': 'dipindah', 'keterangan': 'Kamera dipindah ke bench 2',
                'geom_json': geojson(square(*CAMERA)),
                'tangkapan_layar': SimpleUploadedFile('layar.png', PNG, content_type='image/png')}
        data.update(extra)
        return self.client.post(reverse('cctv:coverage_create'), data)

    def review(self, pengajuan, keputusan, checklist=(), catatan=''):
        return self.client.post(reverse('cctv:coverage_review', args=[pengajuan.pk]),
                                {'keputusan': keputusan, 'checklist': list(checklist), 'catatan': catatan})


class GeometryCheckTests(CoverageTestMixin, TestCase):
    def test_accepts_a_polygon_near_the_camera(self):
        geom = coverage.parse_geometry(geojson(square(*CAMERA)))
        self.assertIsInstance(geom, MultiPolygon)
        self.assertIn('luas', coverage.validate_geometry(geom, self.cctv))

    def test_rejects_bad_shapes(self):
        cctv = Cctv.objects.select_related('spec').get(pk=self.cctv.pk)
        far = MultiPolygon(square(CAMERA[0] + 0.01, CAMERA[1]), srid=4326)  # ~1.1 km away, radius 300 m
        bowtie = MultiPolygon(Polygon(((117.6, 2.2), (117.601, 2.201), (117.601, 2.2), (117.6, 2.201),
                                       (117.6, 2.2)), srid=4326), srid=4326)
        outside = MultiPolygon(square(110.0, 2.2), srid=4326)
        for geom, message in ((far, 'melebihi batas'), (bowtie, 'tidak valid'), (outside, 'luar wilayah')):
            with self.assertRaisesMessage(coverage.GeometryError, message):
                coverage.validate_geometry(geom, cctv)
        with self.assertRaises(coverage.GeometryError):
            coverage.parse_geometry('{"type": "Point", "coordinates": [117.6, 2.2]}')


class SubmitTests(CoverageTestMixin, TestCase):
    def test_mitra_submits_and_it_waits_for_sgi(self):
        self.client.force_login(self.mitra)
        response = self.post_request()
        pengajuan = PengajuanCoverage.objects.get()
        self.assertRedirects(response, f"{reverse('cctv:coverage_index')}?status=menunggu_sgi&id={pengajuan.pk}")
        self.assertEqual((pengajuan.status, pengajuan.versi, pengajuan.digitasi_oleh), ('menunggu_sgi', 1, self.mitra))
        self.assertEqual(list(pengajuan.riwayat.values_list('aksi', flat=True)), ['diajukan', 'digitasi', 'validasi'])

    def test_invalid_geometry_or_missing_screenshot_is_not_sent(self):
        self.client.force_login(self.mitra)
        response = self.post_request(geom_json=geojson(square(CAMERA[0] + 0.01, CAMERA[1])))
        self.assertContains(response, 'melebihi batas')
        response = self.post_request(tangkapan_layar=SimpleUploadedFile('x.png', b'not an image'))
        self.assertContains(response, 'File harus gambar')
        self.assertFalse(PengajuanCoverage.objects.exists())

    def test_cannot_submit_outside_edit_scope_or_twice(self):
        self.client.force_login(self.mitra)
        response = self.post_request(no_cctv=self.other.no_cctv)
        self.assertFalse(PengajuanCoverage.objects.exists())
        self.assertEqual(response.status_code, 200)

        self.post_request()
        response = self.post_request()
        self.assertContains(response, 'belum selesai')
        self.assertEqual(PengajuanCoverage.objects.count(), 1)

    def test_withdrawal_needs_an_active_coverage_but_no_drawing(self):
        self.client.force_login(self.mitra)
        response = self.post_request(jenis='dicabut', geom_json='', tangkapan_layar='')
        self.assertContains(response, 'belum punya coverage aktif')

        CctvCoverage.objects.create(cctv=self.cctv, minggu='2026-10-26', geom=MultiPolygon(square(*CAMERA)))
        self.post_request(jenis='dicabut', geom_json='', tangkapan_layar='')
        pengajuan = PengajuanCoverage.objects.get()
        self.assertEqual((pengajuan.geom, pengajuan.versi), (None, 2))


class ReviewFlowTests(CoverageTestMixin, TestCase):
    def submitted(self):
        self.client.force_login(self.mitra)
        self.post_request()
        self.client.force_login(self.reviewer)
        return PengajuanCoverage.objects.get()

    def test_sgi_then_safety_activates_the_new_coverage(self):
        pengajuan = self.submitted()
        sgi = [k for k, _ in PengajuanCoverage.CHECKLIST_SGI]
        safety = [k for k, _ in PengajuanCoverage.CHECKLIST_SAFETY]

        self.review(pengajuan, 'setujui', sgi[:2])  # not every item checked
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'menunggu_sgi')

        self.review(pengajuan, 'setujui', sgi)
        pengajuan.refresh_from_db()
        self.assertEqual((pengajuan.status, pengajuan.checklist_sgi), ('menunggu_safety', sgi))
        self.assertFalse(CctvCoverage.objects.exists())

        self.review(pengajuan, 'setujui', safety)
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'selesai')
        active, versi = coverage.active_coverage(self.cctv)
        self.assertTrue(active.equals(pengajuan.geom))
        self.assertEqual(versi, 1)

    def test_return_needs_a_note_then_mitra_revises(self):
        pengajuan = self.submitted()
        self.review(pengajuan, 'kembalikan')
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'menunggu_sgi')

        self.review(pengajuan, 'kembalikan', catatan='Timbunan di sisi timur belum dikeluarkan')
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'dikembalikan')

        self.client.force_login(self.mitra)
        page = self.client.get(reverse('cctv:coverage_index'), {'status': 'dikembalikan', 'id': pengajuan.pk})
        self.assertContains(page, reverse('cctv:coverage_revise', args=[pengajuan.pk]))
        smaller = square(*CAMERA, size=0.0005)
        response = self.client.post(reverse('cctv:coverage_revise', args=[pengajuan.pk]), {
            'jenis': 'dipindah', 'keterangan': 'Revisi', 'geom_json': geojson(smaller),
        })
        self.assertEqual(response.status_code, 302)
        pengajuan.refresh_from_db()
        self.assertEqual((pengajuan.status, pengajuan.checklist_sgi), ('menunggu_sgi', []))
        self.assertTrue(pengajuan.geom.equals(MultiPolygon(smaller)))
        self.assertTrue(pengajuan.tangkapan_layar)  # kept from the first submission

    def test_approved_withdrawal_leaves_no_active_coverage(self):
        CctvCoverage.objects.create(cctv=self.cctv, minggu='2026-10-19', geom=MultiPolygon(square(*CAMERA)))
        self.client.force_login(self.mitra)
        self.post_request(jenis='dicabut', geom_json='', tangkapan_layar='')
        self.client.force_login(self.reviewer)
        pengajuan = PengajuanCoverage.objects.get()
        self.review(pengajuan, 'setujui', [k for k, _ in PengajuanCoverage.CHECKLIST_SGI])
        self.review(pengajuan, 'setujui', [k for k, _ in PengajuanCoverage.CHECKLIST_SAFETY])
        active, versi = coverage.active_coverage(self.cctv)
        self.assertIsNone(active)
        self.assertEqual(versi, 2)


class PageAccessTests(CoverageTestMixin, TestCase):
    def test_mitra_cannot_review_and_sees_only_own_scope(self):
        other_request = PengajuanCoverage.objects.create(cctv=self.other, jenis='baru', versi=1,
                                                         geom=MultiPolygon(square(117.41, 2.16)))
        self.client.force_login(self.mitra)
        self.post_request()
        own = PengajuanCoverage.objects.get(cctv=self.cctv)

        page = self.client.get(reverse('cctv:coverage_index'))
        self.assertEqual([p.pk for p in page.context['queue']], [own.pk])
        self.assertFalse(page.context['can_review'])
        self.assertEqual(self.review(own, 'setujui').status_code, 403)
        self.assertEqual(self.client.get(reverse('cctv:coverage_tangkapan', args=[other_request.pk])).status_code, 404)

        screenshot = self.client.get(reverse('cctv:coverage_tangkapan', args=[own.pk]))
        self.assertEqual(b''.join(screenshot.streaming_content), PNG)

    def test_reviewer_page_shows_comparison(self):
        CctvCoverage.objects.create(cctv=self.cctv, minggu='2026-10-19', geom=MultiPolygon(square(*CAMERA, 0.0008)))
        self.client.force_login(self.mitra)
        self.post_request()
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse('cctv:coverage_index'))
        self.assertTrue(page.context['can_review'])
        self.assertEqual((page.context['versi_aktif'], page.context['p'].versi), (1, 2))
        self.assertGreater(page.context['luas_usulan'], page.context['luas_aktif'])
        self.assertContains(page, 'Batas sesuai objek di orthophoto')
        self.assertEqual(page.context['tanda_safety'], 'berikutnya')

    def test_user_without_cctv_scope_is_refused(self):
        self.client.force_login(get_user_model().objects.create_user('lain', password='x'))
        self.assertEqual(self.client.get(reverse('cctv:coverage_index')).status_code, 403)
