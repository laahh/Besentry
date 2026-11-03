import datetime
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
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Perusahaan, Site
from navigation import roles

from . import coverage, snapshot
from .models import CakupanCctv, Cctv, CctvCoverage, CctvSpec, PengajuanCoverage, SnapshotMingguan

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64
CAMERA = (117.60, 2.20)
SGI_ALL = [key for key, _ in PengajuanCoverage.CHECKLIST_SGI]
SAFETY_ALL = [key for key, _ in PengajuanCoverage.CHECKLIST_SAFETY]


def square(x, y, size=0.001):
    """A small square near (x, y): ~110 m per 0.001 degree here."""
    return Polygon(((x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)), srid=4326)


def geojson(polygon):
    return json.dumps({'type': 'Polygon', 'coordinates': [list(map(list, polygon.coords[0]))]})


def shp_zip(polygons, srs='EPSG:32650', parts=('.shp', '.shx', '.dbf', '.prj')):
    """A zipped shapefile made with ogr2ogr from WGS 84 polygons, reprojected to `srs`."""
    folder = Path(tempfile.mkdtemp())
    try:
        features = [{'type': 'Feature', 'properties': {}, 'geometry': json.loads(geojson(p))} for p in polygons]
        (folder / 'in.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
        subprocess.run(['ogr2ogr', '-t_srs', srs, '-f', 'ESRI Shapefile', str(folder / 'boundary.shp'),
                        str(folder / 'in.geojson')], check=True, capture_output=True)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for ext in parts:
                archive.write(folder / f'boundary{ext}', f'boundary{ext}')
        return SimpleUploadedFile('boundary.zip', buffer.getvalue(), content_type='application/zip')
    finally:
        shutil.rmtree(folder, ignore_errors=True)


class FlowMixin:
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.site, cls.other_site = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal', kode='BC')
        cls.cctv = Cctv.objects.create(no_cctv='LMO-BC-0014', nama='Pit J', site=cls.site, jenis='fixed',
                                       perusahaan=cls.perusahaan, posisi=Point(*CAMERA, srid=4326))
        CctvSpec.objects.create(cctv=cls.cctv, radius_m=300, azimuth_deg=135)

        cls.it = User.objects.create_user('it', password='x')
        cls.it.groups.add(Group.objects.get(name=roles.IT_PAMA))
        lmo = Group.objects.create(name='CCTV LMO - PT Berau Coal')
        CakupanCctv.objects.create(group=lmo, site=cls.site, perusahaan=cls.perusahaan)
        smo = Group.objects.create(name='CCTV SMO - PT Berau Coal')
        CakupanCctv.objects.create(group=smo, site=cls.other_site, perusahaan=cls.perusahaan)
        cls.mitra = User.objects.create_user('mitra', password='x')
        cls.mitra.groups.add(Group.objects.get(name=roles.SURVEY_MITRA), lmo)
        cls.mitra_smo = User.objects.create_user('mitra-smo', password='x')
        cls.mitra_smo.groups.add(Group.objects.get(name=roles.SURVEY_MITRA), smo)
        cls.survey_bc = User.objects.create_user('survey-bc', password='x')
        cls.survey_bc.groups.add(Group.objects.get(name=roles.SURVEY_BC))
        cls.safety = User.objects.create_user('safety', password='x')
        cls.safety.groups.add(Group.objects.get(name=roles.SAFETY))

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def cctv_form(self, **extra):
        data = {
            'no_cctv': self.cctv.no_cctv, 'nama': 'Pit J', 'site': self.site.pk, 'perusahaan': self.perusahaan.pk,
            'jenis': 'fixed', 'status': 'live', 'kondisi': 'baik', 'longitude': str(CAMERA[0]),
            'latitude': str(CAMERA[1]), 'spec-radius_m': '300', 'spec-azimuth_deg': '135',
        }
        data.update(extra)
        return data

    def ajukan(self, **changes):
        """IT opens a request from the CCTV page and continues to digitisation."""
        self.client.force_login(self.it)
        self.client.post(reverse('cctv:ajukan', args=[self.cctv.pk]))
        pengajuan = coverage.open_request(self.cctv) or PengajuanCoverage.objects.latest('dibuat')
        data = {**self.cctv_form(**changes), 'aksi': 'lanjutkan',
                'tangkapan_layar': SimpleUploadedFile('layar.png', PNG, content_type='image/png')}
        self.response = self.client.post(reverse('cctv:pengajuan_it', args=[pengajuan.pk]), data)
        pengajuan.refresh_from_db()
        return pengajuan

    def digitasi(self, pengajuan, aksi='kirim', polygon=None, **extra):
        self.client.force_login(self.mitra)
        data = {'geom_json': geojson(polygon or square(*CAMERA)), 'aksi': aksi, **extra}
        self.response = self.client.post(reverse('cctv:pengajuan_mitra', args=[pengajuan.pk]), data)
        pengajuan.refresh_from_db()
        return pengajuan

    def review(self, pengajuan, who, aksi, checklist=(), catatan=''):
        user, url = {'sgi': (self.survey_bc, 'cctv:pengajuan_sgi'), 'safety': (self.safety, 'cctv:pengajuan_safety')}[who]
        self.client.force_login(user)
        self.response = self.client.post(reverse(url, args=[pengajuan.pk]),
                                         {'aksi': aksi, 'checklist': list(checklist), 'catatan': catatan})
        pengajuan.refresh_from_db()
        return pengajuan

    def moved(self):
        return self.ajukan(longitude='117.6002')  # ~22 m: a geometry change


class FlowTests(FlowMixin, TestCase):
    def test_full_flow_to_approved(self):
        pengajuan = self.moved()
        self.assertEqual((pengajuan.status, pengajuan.jenis), ('menunggu_mitra', 'dipindah'))
        self.assertEqual(pengajuan.minggu, coverage.this_monday())
        self.assertEqual(pengajuan.perubahan, [['Koordinat', '2.200000, 117.600000', '2.200000, 117.600200']])
        self.assertEqual(pengajuan.posisi_sebelum.coords, CAMERA)
        self.cctv.refresh_from_db()
        self.assertEqual(self.cctv.posisi.x, 117.6002)  # IT's change applies when IT continues

        self.assertEqual(self.digitasi(pengajuan).status, 'menunggu_sgi')
        self.assertEqual(self.review(pengajuan, 'sgi', 'safety', SGI_ALL).status, 'menunggu_safety')
        self.assertEqual(self.review(pengajuan, 'safety', 'approve', SAFETY_ALL, 'Layak').status, 'approved')

        coverage_row = CctvCoverage.objects.get(cctv=self.cctv)
        self.assertEqual(coverage_row.minggu, pengajuan.minggu)
        self.assertEqual(pengajuan.label, f'Approved {pengajuan.minggu:W%V %G}')

        listed = self.client.get(reverse('cctv:index'))
        self.assertContains(listed, f'Approved {pengajuan.minggu:W%V %G}')
        self.assertContains(listed, f'{reverse("cctv:pengajuan", args=[pengajuan.pk])}?tab=selesai')

    def test_no_geometry_change_is_approved_at_once(self):
        pengajuan = self.ajukan(kondisi='breakdown')
        self.assertEqual((pengajuan.status, pengajuan.jenis, pengajuan.perubahan), ('approved', 'ubah', []))
        self.assertRedirects(self.response, reverse('cctv:detail', args=[self.cctv.pk]))
        self.cctv.refresh_from_db()
        self.assertEqual(self.cctv.kondisi, 'breakdown')

    def test_spec_angles_count_as_geometry(self):
        self.assertEqual(self.ajukan(**{'spec-azimuth_deg': '90'}).status, 'menunggu_mitra')

    def test_draft_does_not_touch_the_cctv(self):
        self.client.force_login(self.it)
        self.client.post(reverse('cctv:ajukan', args=[self.cctv.pk]))
        pengajuan = coverage.open_request(self.cctv)
        self.client.post(reverse('cctv:pengajuan_it', args=[pengajuan.pk]),
                         {**self.cctv_form(nama='Nama baru', **{'akses-password_baru': 'rahasia'}), 'aksi': 'draft'})
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'draft_it')
        self.assertEqual(pengajuan.data_draft['nama'], ['Nama baru'])
        self.assertNotIn('akses-password_baru', pengajuan.data_draft)
        self.cctv.refresh_from_db()
        self.assertEqual(self.cctv.nama, 'Pit J')
        page = self.client.get(reverse('cctv:pengajuan', args=[pengajuan.pk]))
        self.assertContains(page, 'value="Nama baru"')
        # The stored values stay visible: as placeholders, and as a note on what IT changed.
        self.assertContains(page, 'placeholder="Pit J"')
        self.assertContains(page, 'Sebelumnya: Pit J')
        self.assertContains(page, 'placeholder="300.0"')
        self.assertNotContains(page, 'Sebelumnya: 300')  # unchanged fields get no note

    def test_continuing_needs_a_screenshot(self):
        self.client.force_login(self.it)
        self.client.post(reverse('cctv:ajukan', args=[self.cctv.pk]))
        pengajuan = coverage.open_request(self.cctv)
        response = self.client.post(reverse('cctv:pengajuan_it', args=[pengajuan.pk]),
                                    {**self.cctv_form(longitude='117.6002'), 'aksi': 'lanjutkan'})
        self.assertContains(response, 'Upload tangkapan layar CCTV terbaru.')
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'draft_it')

    def test_one_open_request_per_cctv(self):
        first = self.moved()
        self.client.force_login(self.it)
        response = self.client.post(reverse('cctv:ajukan', args=[self.cctv.pk]))
        self.assertRedirects(response, reverse('cctv:pengajuan', args=[first.pk]), fetch_redirect_response=False)
        self.assertEqual(PengajuanCoverage.objects.count(), 1)

    def test_new_cctv_goes_through_the_whole_flow(self):
        self.client.force_login(self.it)
        data = {**self.cctv_form(no_cctv='LMO-BC-0100'), 'aksi': 'draft'}
        self.client.post(reverse('cctv:pengajuan_baru'), data)
        draft = PengajuanCoverage.objects.get(cctv__isnull=True)
        self.assertEqual(draft.no_cctv, 'LMO-BC-0100')
        self.assertFalse(Cctv.objects.filter(no_cctv='LMO-BC-0100').exists())
        self.assertContains(self.client.get(reverse('cctv:index')), 'LMO-BC-0100')  # in the draft list

        data = {**self.cctv_form(no_cctv='LMO-BC-0100'), 'aksi': 'lanjutkan',
                'tangkapan_layar': SimpleUploadedFile('layar.png', PNG, content_type='image/png')}
        self.client.post(reverse('cctv:pengajuan_it', args=[draft.pk]), data)
        draft.refresh_from_db()
        self.assertEqual((draft.cctv.no_cctv, draft.jenis, draft.status), ('LMO-BC-0100', 'baru', 'menunggu_mitra'))
        self.assertIsNone(draft.posisi_sebelum)

    def test_mitra_draft_shows_on_the_map_before_approval(self):
        pengajuan = self.digitasi(self.moved(), aksi='draft')
        self.assertEqual(pengajuan.status, 'menunggu_mitra')
        self.assertEqual(coverage.map_coverage(self.cctv), pengajuan.geom)
        self.client.force_login(self.it)
        self.assertContains(self.client.get(reverse('cctv:detail', args=[self.cctv.pk])), 'belum Approved')

    def test_mitra_outside_their_scope_cannot_digitise(self):
        pengajuan = self.moved()
        self.client.force_login(self.mitra_smo)
        url = reverse('cctv:pengajuan_mitra', args=[pengajuan.pk])
        self.assertEqual(self.client.post(url, {'geom_json': geojson(square(*CAMERA)), 'aksi': 'kirim'}).status_code, 404)

    def test_survey_bc_needs_the_whole_checklist(self):
        pengajuan = self.digitasi(self.moved())
        self.assertEqual(self.review(pengajuan, 'sgi', 'safety', SGI_ALL[:2]).status, 'menunggu_sgi')
        self.assertEqual(self.review(pengajuan, 'sgi', 'mitra').status, 'menunggu_sgi')  # a note is required
        self.assertEqual(self.review(pengajuan, 'sgi', 'mitra', SGI_ALL[:2], 'Bench 3 terhalang').status,
                         'return_mitra')
        self.assertEqual(self.digitasi(pengajuan).status, 'menunggu_sgi')

    def test_safety_warning_and_returns(self):
        pengajuan = self.review(self.digitasi(self.moved()), 'sgi', 'safety', SGI_ALL)
        self.assertEqual(self.review(pengajuan, 'safety', 'approve', SAFETY_ALL).status, 'menunggu_safety')  # no feedback
        self.assertEqual(self.review(pengajuan, 'safety', 'sgi', [], 'Boundary terlalu jauh').status, 'return_sgi')
        self.assertEqual(self.review(pengajuan, 'sgi', 'safety', SGI_ALL).status, 'menunggu_safety')
        approved = self.review(pengajuan, 'safety', 'approve', SAFETY_ALL[:3], 'Lensa berdebu')
        self.assertEqual((approved.status, approved.feedback_safety), ('approved_warning', 'Lensa berdebu'))
        self.client.force_login(self.it)
        detail = self.client.get(reverse('cctv:detail', args=[self.cctv.pk]))
        self.assertContains(detail, 'Warning Safety')
        self.assertContains(detail, PengajuanCoverage.CHECKLIST_SAFETY[3][1])  # the one left unticked
        self.assertNotContains(detail, PengajuanCoverage.CHECKLIST_SAFETY[1][1])

    def test_return_to_it_restarts_with_a_new_week(self):
        pengajuan = self.review(self.review(self.digitasi(self.moved()), 'sgi', 'safety', SGI_ALL),
                                'safety', 'it', [], 'Kamera salah tiang')
        self.assertEqual(pengajuan.status, 'return_it')
        next_week = pengajuan.minggu + datetime.timedelta(days=7)
        with mock.patch.object(coverage, 'this_monday', return_value=next_week):
            self.client.force_login(self.it)
            data = {**self.cctv_form(longitude='117.6004'), 'aksi': 'lanjutkan'}
            self.client.post(reverse('cctv:pengajuan_it', args=[pengajuan.pk]), data)
        pengajuan.refresh_from_db()
        self.assertEqual((pengajuan.status, pengajuan.minggu), ('menunggu_mitra', next_week))
        self.assertEqual(pengajuan.posisi_sebelum.coords, CAMERA)  # still the original "before"

    def test_new_screenshot_after_return_gets_a_new_url(self):
        pengajuan = self.review(self.digitasi(self.moved()), 'sgi', 'it', [], 'Foto buram')
        old_url = pengajuan.tangkapan_url
        self.client.force_login(self.it)
        data = {**self.cctv_form(longitude='117.6002'), 'aksi': 'draft',
                'tangkapan_layar': SimpleUploadedFile('layar.png', PNG + b'baru', content_type='image/png')}
        self.client.post(reverse('cctv:pengajuan_it', args=[pengajuan.pk]), data)
        pengajuan.refresh_from_db()
        self.assertNotEqual(pengajuan.tangkapan_url, old_url)
        self.assertTrue(pengajuan.tangkapan_layar.read().endswith(b'baru'))
        self.assertContains(self.client.get(reverse('cctv:pengajuan', args=[pengajuan.pk]), {'tab': 'it'}),
                            pengajuan.tangkapan_url)

    def test_wrong_role_cannot_act(self):
        pengajuan = self.digitasi(self.moved())
        self.client.force_login(self.safety)
        url = reverse('cctv:pengajuan_sgi', args=[pengajuan.pk])
        self.assertEqual(self.client.post(url, {'aksi': 'safety', 'checklist': SGI_ALL}).status_code, 403)
        self.client.force_login(self.mitra)
        self.assertEqual(self.client.get(reverse('cctv:pengajuan', args=[pengajuan.pk])).status_code, 200)

    def test_list_filters_on_request_status(self):
        self.moved()
        self.client.force_login(self.survey_bc)
        self.assertEqual(self.client.get(reverse('cctv:index'), {'pengajuan': 'menunggu_mitra'}).context['stats']['total'], 1)
        self.assertEqual(self.client.get(reverse('cctv:index'), {'pengajuan': 'approved'}).context['stats']['total'], 0)


class GeometryTests(FlowMixin, TestCase):
    def test_limits(self):
        cctv = Cctv.objects.select_related('spec').get(pk=self.cctv.pk)
        self.assertIn('luas', coverage.validate_geometry(MultiPolygon(square(*CAMERA), srid=4326), cctv))
        far = MultiPolygon(square(CAMERA[0] + 0.01, CAMERA[1]), srid=4326)  # ~1.1 km away, radius 300 m
        big = MultiPolygon(square(CAMERA[0], CAMERA[1], size=0.015), srid=4326)  # ~270 ha
        outside = MultiPolygon(square(110.0, -5.0), srid=4326)
        for geom, message in ((far, 'melebihi batas'), (big, 'kurang dari 200 ha'), (outside, 'luar batas site')):
            with self.subTest(message), self.assertRaisesMessage(coverage.GeometryError, message):
                coverage.validate_geometry(geom, cctv)

    def test_shp_loads_as_an_editable_polygon(self):
        pengajuan = self.moved()
        self.client.force_login(self.mitra)
        cek = reverse('cctv:pengajuan_mitra_cek', args=[pengajuan.pk])
        result = self.client.post(cek, {'file_shp': shp_zip([square(*CAMERA)])}).json()
        self.assertEqual((result['nama'], result['valid'], result['geom']['type']), ('boundary.zip', True, 'Polygon'))
        self.assertAlmostEqual(result['geom']['coordinates'][0][0][0], CAMERA[0], places=5)
        pengajuan.refresh_from_db()
        self.assertIsNone(pengajuan.geom)  # only read, not saved

        # Edited in the browser, then sent: still recorded as coming from the SHP.
        ring = result['geom']['coordinates'][0]
        ring[2] = [ring[2][0] + 0.0002, ring[2][1]]
        data = {'aksi': 'kirim', 'nama_shp': result['nama'],
                'geom_json': json.dumps({'type': 'Polygon', 'coordinates': [ring]})}
        self.client.post(reverse('cctv:pengajuan_mitra', args=[pengajuan.pk]), data)
        pengajuan.refresh_from_db()
        self.assertEqual((pengajuan.status, pengajuan.sumber_geom, pengajuan.file_shp),
                         ('menunggu_sgi', 'shp', 'boundary.zip'))
        self.assertAlmostEqual(pengajuan.geom[0].exterior_ring[2][0], ring[2][0], places=7)

    def test_check_reports_without_saving(self):
        pengajuan = self.moved()
        self.client.force_login(self.mitra)
        cek = reverse('cctv:pengajuan_mitra_cek', args=[pengajuan.pk])
        far = self.client.post(cek, {'geom_json': geojson(square(CAMERA[0] + 0.01, CAMERA[1]))}).json()
        self.assertFalse(far['valid'])
        self.assertIn('melebihi batas', far['pesan'])
        bad = self.client.post(cek, {'file_shp': shp_zip([square(*CAMERA)], srs='EPSG:4326')})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('UTM zone 50N', bad.json()['error'])
        self.client.force_login(self.survey_bc)
        self.assertEqual(self.client.post(cek, {'geom_json': geojson(square(*CAMERA))}).status_code, 403)

    def test_editable_polygon(self):
        holed = Polygon(square(*CAMERA).exterior_ring, square(CAMERA[0] + 0.0004, CAMERA[1] + 0.0004, 0.0002).exterior_ring)
        two = MultiPolygon(square(*CAMERA), square(CAMERA[0] + 0.002, CAMERA[1]), srid=4326)
        for geom, message in ((MultiPolygon(holed, srid=4326), 'lubang'), (two, '2 polygon')):
            with self.subTest(message), self.assertRaisesMessage(coverage.GeometryError, message):
                coverage.editable_polygon(geom)
        circle = Point(*CAMERA, srid=4326).transform(coverage.UTM_50N, clone=True).buffer(100, quadsegs=250)
        circle.transform(4326)
        polygon, note = coverage.editable_polygon(MultiPolygon(circle, srid=4326))
        self.assertLessEqual(len(polygon.exterior_ring) - 1, coverage.EDIT_MAX_VERTICES)
        self.assertIn('disederhanakan', note)

    def test_previous_screenshot_on_the_mitra_tab(self):
        first = self.review(self.review(self.digitasi(self.moved()), 'sgi', 'safety', SGI_ALL),
                            'safety', 'approve', SAFETY_ALL, 'OK')
        second = self.ajukan(longitude='117.6004')
        self.client.force_login(self.mitra)
        page = self.client.get(reverse('cctv:pengajuan', args=[second.pk]), {'tab': 'mitra'})
        self.assertEqual(page.context['sebelumnya'], first)
        self.assertContains(page, reverse('cctv:pengajuan_tangkapan', args=[first.pk]))

    def test_shp_rules(self):
        cases = (
            (shp_zip([square(*CAMERA), square(CAMERA[0], CAMERA[1] + 0.002)]), 'tepat 1 feature (ada 2)'),
            (shp_zip([square(*CAMERA)], parts=('.shp', '.shx', '.dbf')), 'tidak ada .prj'),
            (shp_zip([square(*CAMERA)], srs='EPSG:4326'), 'UTM zone 50N'),
            (SimpleUploadedFile('a.zip', b'bukan zip'), 'harus berupa .zip'),
        )
        for upload, message in cases:
            with self.subTest(message), self.assertRaisesMessage(coverage.GeometryError, message):
                coverage.read_shp_zip(upload)


class SnapshotTests(FlowMixin, TestCase):
    def test_closing_a_week_once(self):
        pengajuan = self.digitasi(self.moved(), aksi='draft')
        minggu = datetime.date(2026, 10, 26)
        self.assertEqual(snapshot.close_week(minggu), 1)
        row = SnapshotMingguan.objects.get()
        self.assertEqual((row.no_cctv, row.status_pengajuan, row.minggu_pengajuan),
                         ('LMO-BC-0014', 'menunggu_mitra', pengajuan.minggu))
        self.assertEqual(row.coverage, pengajuan.geom)  # Mitra's boundary counts once drawn
        self.assertEqual(row.data['spec']['radius_m'], 300)

        self.cctv.delete()
        self.assertEqual(snapshot.close_week(minggu), 0)  # locked: never rewritten
        self.assertTrue(SnapshotMingguan.objects.filter(no_cctv='LMO-BC-0014').exists())
