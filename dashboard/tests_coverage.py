import datetime
import io
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import MultiPolygon, Point
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from areakerja import kalkulasi, services
from areakerja.models import JenisArea, MitraSite, SnapshotCoverage
from areakerja.tests import setujui_semua
from cctv.models import CakupanCctv, Cctv, CctvCoverage
from cctv.snapshot import close_week
from cctv.tests_pengajuan import CAMERA, shp_zip, square
from core.models import Perusahaan, Site
from navigation import roles
from navigation.menu import build_menu

from . import coverage
from .coverage import Filter

W45 = datetime.date(2026, 11, 2)
W46 = W45 + datetime.timedelta(days=7)
X, Y = CAMERA
MONDAY_PATCHES = ('areakerja.services.this_monday', 'areakerja.views.this_monday', 'areakerja.kalkulasi.this_monday',
                  'dashboard.coverage.this_monday', 'dashboard.coverage_views.this_monday')


class DashboardCoverageTests(TestCase):
    """The dashboard reads the coverage already computed: live this week, the locked snapshot for a closed one."""

    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.site = Site.objects.create(kode='LMO')
        cls.pama = Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')
        cls.mitra_site = MitraSite.objects.create(site=cls.site, perusahaan=cls.pama)
        cls.survey_bc = User.objects.create_user('survey-bc', password='x')
        cls.survey_bc.groups.add(Group.objects.get(name=roles.SURVEY_BC))
        cls.mining = User.objects.create_user('mining', password='x')
        cls.mining.groups.add(Group.objects.get(name=roles.MINING))
        lmo = Group.objects.create(name='CCTV LMO - PT Pamapersada Nusantara')
        CakupanCctv.objects.create(group=lmo, site=cls.site, perusahaan=cls.pama)
        cls.mitra = User.objects.create_user('mitra', password='x')
        cls.mitra.groups.add(Group.objects.get(name=roles.SURVEY_MITRA), lmo)

    def setUp(self):
        transaction.get_connection().run_on_commit.clear()
        self.week(W45)

    def week(self, monday):
        """Pretend today is in the week starting `monday`."""
        for target in MONDAY_PATCHES:
            patcher = mock.patch(target, return_value=monday)
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def committed(self):
        pending = transaction.get_connection().run_on_commit
        start = len(pending)
        with self.captureOnCommitCallbacks(execute=True):
            yield
        del pending[start:]

    def cctv(self, no, boundary, **fields):
        with self.committed():
            cctv = Cctv.objects.create(no_cctv=no, nama=no, site=self.site, perusahaan=self.pama, jenis='fixed',
                                       posisi=Point(X, Y, srid=4326), **fields)
            CctvCoverage.objects.create(cctv=cctv, minggu=W45, geom=MultiPolygon(boundary, srid=4326))
        return cctv

    def area(self, approve=True, minggu=W45):
        """The week's pit: a 0.004 degree square (~19.7 ha); the other kinds 'tidak ada'."""
        with mock.patch('areakerja.services.this_monday', return_value=minggu), self.committed():
            pengajuan = services.request_for(self.mitra_site, self.survey_bc)
            services.save_slot(pengajuan, self.survey_bc, 'pit', 'upload', shp_zip([square(X, Y, 0.004)]))
            for jenis in [j.value for j in JenisArea][1:]:
                services.save_slot(pengajuan, self.survey_bc, jenis, 'tidak_ada')
            services.ajukan(pengajuan, self.survey_bc)
            if approve:
                setujui_semua(pengajuan, self.survey_bc)
        return pengajuan

    def lock_w45(self):
        """Monday 00:00 of W46: the CCTV snapshot, then the coverage snapshot of W45."""
        close_week(W45)
        with mock.patch('areakerja.kalkulasi.this_monday', return_value=W46):
            kalkulasi.tutup_minggu(W45)

    def get(self, name, user=None, args=(), **query):
        self.client.force_login(user or self.survey_bc)
        return self.client.get(reverse(f'coverage:{name}', args=args), query)

    def test_only_bc_and_admin_open_it(self):
        for user, status in ((self.survey_bc, 200), (self.mining, 200), (self.mitra, 403)):
            with self.subTest(user=user.username):
                self.assertEqual(self.get('index', user).status_code, status)
                self.assertEqual(self.get('cctv', user).status_code, status)
        labels = lambda user: {item['title'] for section in build_menu(user) for top in section['items']
                               for item in (top['children'] or [top])}
        self.assertIn('Dashboard Coverage', labels(self.survey_bc))
        self.assertNotIn('Dashboard Coverage', labels(self.mitra))

    def test_running_week_reads_the_live_numbers(self):
        self.area()
        self.cctv('A', square(X, Y, 0.002))  # a quarter of the pit
        response = self.get('index')
        kpi = response.context['kpi']
        self.assertAlmostEqual(kpi['persen']['tercover'], 25.0, delta=0.5)
        self.assertEqual(kpi['mitra_site'], 1)
        (group,) = response.context['tabel']
        self.assertEqual((group['site'], [j['jenis'] for j in group['jenis']]), ('LMO', ['pit']))  # empty kinds left out
        self.assertEqual(response.context['tren'][-1]['label'], 'W45 2026')
        self.assertAlmostEqual(response.context['tren'][-1]['persen']['tercover'], 25.0, delta=0.5)
        self.assertContains(response, 'week berjalan')

        semua = self.get('index', mode='semua').context['kpi']
        self.assertAlmostEqual(semua['persen']['tercover'], 25.0, delta=0.5)
        self.assertEqual(self.get('index', jenis='hauling').context['kpi']['luas'], 0)

    def test_closed_week_reads_the_locked_snapshot(self):
        self.area()
        self.cctv('A', square(X, Y, 0.002))
        self.lock_w45()
        self.week(W46)
        self.cctv('B', square(X + 0.002, Y, 0.002))  # after the lock: only the live week moves
        locked = self.get('index', minggu='2026-11-04')  # any day of W45
        self.assertEqual(locked.context['f'].minggu, W45)
        self.assertAlmostEqual(locked.context['kpi']['persen']['tercover'], 25.0, delta=0.5)
        self.assertContains(locked, 'Angka week terkunci')
        live = self.get('index')
        self.assertAlmostEqual(live.context['kpi']['persen']['tercover'], 50.0, delta=0.5)
        tren = {p['label']: p['persen']['tercover'] for p in live.context['tren']}
        self.assertAlmostEqual(tren['W45 2026'], 25.0, delta=0.5)
        self.assertAlmostEqual(tren['W46 2026'], 50.0, delta=0.5)
        self.assertIsNone(tren['W44 2026'])  # no numbers that week
        self.assertEqual(coverage.daftar_minggu(), [W46, W45])

    def test_week_waiting_for_its_area(self):
        self.area(minggu=W45 - datetime.timedelta(days=7))  # last week's area exists, so W45 waits for its own
        self.area(approve=False)
        self.lock_w45()
        self.week(W46)
        response = self.get('index', minggu=W45.isoformat())
        self.assertEqual([m['site'] for m in response.context['menunggu']], ['LMO'])
        self.assertEqual(response.context['tabel'], [])
        self.assertContains(response, 'menunggu area kerja Approved')

    def test_map_layers(self):
        self.area()
        self.cctv('A', square(X, Y, 0.002))
        self.cctv('M', square(X + 0.002, Y + 0.002, 0.002), status='offline')
        tercover = self.get('lapisan', args=['tercover']).json()
        (feature,) = tercover['features']
        self.assertEqual((feature['properties']['site'], feature['properties']['jenis']), ('LMO', 'pit'))
        self.assertIn(feature['geometry']['type'], ('Polygon', 'MultiPolygon'))  # simplified in the database
        self.assertEqual(len(self.get('lapisan', args=['potensi']).json()['features']), 1)
        self.assertEqual(self.get('lapisan', args=['lainnya']).status_code, 404)
        self.assertEqual([f['properties']['jenis'] for f in self.get('area').json()['features']], ['pit'])
        points = {f['properties']['no']: f['properties']['kelompok'] for f in self.get('cctv').json()['features']}
        self.assertEqual(points, {'A': 'aktif', 'M': 'mati'})

        self.lock_w45()
        self.week(W46)
        Cctv.objects.filter(no_cctv='M').update(kondisi='dismantle')
        old = {f['properties']['no']: f['properties']['kondisi']
               for f in self.get('cctv', minggu=W45.isoformat()).json()['features']}
        self.assertEqual(old['M'], 'baik')  # the CCTV as they stood when W45 closed
        self.assertEqual(len(self.get('area', minggu=W45.isoformat()).json()['features']), 1)
        self.assertEqual(len(self.get('lapisan', args=['tercover'], minggu=W45.isoformat()).json()['features']), 1)

    def test_cctv_boundaries(self):
        self.area()
        self.cctv('A', square(X, Y, 0.002))
        self.cctv('M', square(X + 0.002, Y, 0.002), status='offline')
        live = {f['properties']['no']: f['properties']['kelompok'] for f in self.get('boundary').json()['features']}
        self.assertEqual(live, {'A': 'aktif', 'M': 'mati'})
        self.lock_w45()
        self.week(W46)
        Cctv.objects.filter(no_cctv='M').update(kondisi='dismantle')
        locked = {f['properties']['no']: f['properties']['kondisi'] for f in self.get('boundary', minggu=W45.isoformat()).json()['features']}
        self.assertEqual(locked, {'A': 'baik', 'M': 'baik'})  # as the CCTV snapshot locked them
        self.assertEqual(self.get('boundary', self.mitra).status_code, 403)

    def test_fullscreen_panel_data(self):
        self.area()
        self.cctv('A', square(X, Y, 0.002))
        data = self.get('data', site=self.site.pk).json()
        self.assertEqual((data['label'], data['minggu'], data['sesudah']), ('W45 2026', '2026-11-02', None))
        self.assertEqual(data['sebelum'], '2026-10-26')
        self.assertEqual(data['kpi_teks']['tercover'], '25,0%')
        self.assertEqual(data['peta']['fit'], 'area')                      # one site: frame its areas
        self.assertIn(f'site={self.site.pk}', data['peta']['lapisan']['tercover'])
        self.assertEqual(self.get('data').json()['peta']['fit'], 'ortho')  # all sites: the orthophoto
        self.assertEqual(self.get('data', self.mitra).status_code, 403)

    def test_timeline_shows_a_year(self):
        weeks = coverage.garis_waktu(Filter(minggu=W45))  # nothing closed yet: a year of empty weeks
        past = weeks
        self.assertEqual((len(past), past[0]['label'], past[-1]['label']), (52, 'W46 2025', 'W45 2026'))
        self.assertEqual({w['status'] for w in past[:-1]}, {'kosong'})
        self.assertEqual(past[-1]['status'], 'live')

        self.area()
        self.cctv('A', square(X, Y, 0.002))
        self.lock_w45()
        self.week(W46 + datetime.timedelta(days=7))  # W47: W46 was never closed
        weeks = {w['w']: w for w in coverage.garis_waktu(Filter.from_query({}))}
        self.assertEqual([weeks[w]['status'] for w in ('W45', 'W46', 'W47')], ['terkunci', 'kosong', 'live'])
        self.assertAlmostEqual(weeks['W45']['tercover'], 25.0, delta=0.5)
        self.assertIsNone(weeks['W46']['tercover'])
        self.assertEqual((weeks['W45']['bulan'], weeks['W45']['rentang']), (11, '02 Nov – 08 Nov 2026'))
        self.assertEqual(len(self.get('data').json()['garis_waktu']), 52)

        old = SnapshotCoverage.objects.get(minggu=W45)
        SnapshotCoverage.objects.create(minggu=W45 - datetime.timedelta(days=7 * 60), site_kode='LMO',
                                        perusahaan_nama=old.perusahaan_nama, status=SnapshotCoverage.Status.TERKUNCI)
        self.assertEqual(len(coverage.garis_waktu(Filter.from_query({}))), 63)  # back to the oldest snapshot

    def test_export_excel(self):
        self.area()
        self.cctv('A', square(X, Y, 0.002))
        response = self.get('export')
        self.assertIn('coverage-W45-2026-milik.xlsx', response['Content-Disposition'])
        sheet = load_workbook(io.BytesIO(response.content)).active
        rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=4)]
        self.assertEqual(rows[0][:3], ['LMO', 'PT Pamapersada Nusantara', 'Pit'])
        self.assertEqual(rows[1][2], 'Total')
        self.assertAlmostEqual(rows[1][7], 25.0, delta=0.5)
        self.assertEqual(rows[-1][0], 'Total semua')

    def test_filter_from_the_query_string(self):
        f = Filter.from_query({'minggu': '2026-11-20', 'mode': 'x', 'jenis': 'pit', 'site': 'abc'})
        self.assertEqual((f.minggu, f.mode, f.jenis, f.site), (W45, 'milik', 'pit', None))  # no future weeks
        self.assertEqual(Filter.from_query({}).minggu, W45)
        self.assertEqual(f.query(), {'minggu': '2026-11-02', 'mode': 'milik', 'jenis': 'pit'})
