import datetime
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import MultiPolygon, Point
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cctv.coverage import GeometryError, ReviewError
from cctv.models import CakupanCctv, Cctv, CctvCoverage
from cctv.snapshot import close_week
from cctv.tests_pengajuan import CAMERA, shp_zip, square
from core.models import Perusahaan, Site
from navigation import roles

from . import kalkulasi, services
from .models import (
    AreaKerja, CoverageLive, JenisArea, MitraSite, PengajuanAreaKerja, RiwayatAreaKerja, SnapshotCoverage,
)

W45 = datetime.date(2026, 11, 2)
W46 = W45 + datetime.timedelta(days=7)
JENIS = [j.value for j in JenisArea]


def this_week(monday):
    """Pretend today is in the week starting `monday` (services and views ask cctv.coverage)."""
    return mock.patch('areakerja.services.this_monday', return_value=monday), \
        mock.patch('areakerja.views.this_monday', return_value=monday)


class MitraSiteTests(TestCase):
    def test_one_row_per_site_and_company(self):
        site, bc = Site.objects.create(kode='LMO'), Perusahaan.objects.create(nama='PT Berau Coal', kode='BC')
        mitra = MitraSite.objects.create(site=site, perusahaan=bc)
        self.assertEqual((str(mitra), mitra.status_label), ('LMO - PT Berau Coal', 'Aktif'))
        mitra.aktif = False
        self.assertEqual(mitra.status_label, 'Operasional Sudah Berhenti')
        with self.assertRaises(IntegrityError):
            MitraSite.objects.create(site=site, perusahaan=bc)

    def test_priority_order(self):
        self.assertEqual(JENIS, ['pit', 'hauling', 'infra_tambang', 'infra_non_tambang'])


@override_settings(MEDIA_ROOT='/tmp/besentry-test-media')
class AreaKerjaTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.site = Site.objects.create(kode='LMO')
        cls.pama = Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')
        cls.bc = Perusahaan.objects.create(nama='PT Berau Coal', kode='BC')
        cls.mitra_site = MitraSite.objects.create(site=cls.site, perusahaan=cls.pama)
        cls.bc_site = MitraSite.objects.create(site=cls.site, perusahaan=cls.bc)
        cls.other = MitraSite.objects.create(site=Site.objects.create(kode='SMO'), perusahaan=cls.pama)
        lmo = Group.objects.create(name='CCTV LMO - PT Pamapersada Nusantara')
        CakupanCctv.objects.create(group=lmo, site=cls.site, perusahaan=cls.pama)
        cls.mitra = User.objects.create_user('mitra', password='x')
        cls.mitra.groups.add(Group.objects.get(name=roles.SURVEY_MITRA), lmo)
        cls.survey_bc = User.objects.create_user('survey-bc', password='x')
        cls.survey_bc.groups.add(Group.objects.get(name=roles.SURVEY_BC))

    def setUp(self):
        for patcher in this_week(W45):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fill(self, pengajuan, user, pit=None, hauling=None):
        """Pit and hauling uploaded (when given), the other kinds 'none'."""
        for jenis, polygons in (('pit', pit), ('hauling', hauling)):
            if polygons:
                services.save_slot(pengajuan, user, jenis, 'upload', shp_zip(polygons))
            else:
                services.save_slot(pengajuan, user, jenis, 'tidak_ada')
        for jenis in ('infra_tambang', 'infra_non_tambang'):
            services.save_slot(pengajuan, user, jenis, 'tidak_ada')
        return PengajuanAreaKerja.objects.prefetch_related('area').get(pk=pengajuan.pk)

    def test_upload_dissolves_and_clips_by_priority(self):
        pengajuan = services.request_for(self.mitra_site, self.mitra)
        # Two touching pit squares -> one area; the hauling square overlaps the pit by half.
        pit = [square(*CAMERA), square(CAMERA[0] + 0.001, CAMERA[1])]
        hauling = [square(CAMERA[0] + 0.0015, CAMERA[1])]
        pengajuan = self.fill(pengajuan, self.mitra, pit=pit, hauling=hauling)
        pit_area, hauling_area = pengajuan.slot('pit'), pengajuan.slot('hauling')
        self.assertEqual((pit_area.jumlah_feature, len(pit_area.geom)), (2, 1))  # dissolved into one polygon
        # Pit = two squares; hauling = one square, half of it under the pit and cut away.
        self.assertAlmostEqual(pit_area.luas_ha, 2 * (hauling_area.luas_ha + hauling_area.dipotong_ha), delta=0.01)
        self.assertAlmostEqual(hauling_area.luas_ha, hauling_area.dipotong_ha, delta=0.01)
        self.assertFalse(pit_area.geom.intersection(hauling_area.geom).area > 1e-12)

    def test_upload_rules(self):
        pengajuan = services.request_for(self.mitra_site, self.mitra)
        with self.assertRaisesMessage(GeometryError, 'di luar batas'):
            services.save_slot(pengajuan, self.mitra, 'pit', 'upload', shp_zip([square(110.0, -5.0)]))
        with self.assertRaisesMessage(GeometryError, 'UTM zone 50N'):
            services.save_slot(pengajuan, self.mitra, 'pit', 'upload', shp_zip([square(*CAMERA)], srs='EPSG:4326'))
        with self.assertRaisesMessage(GeometryError, 'Belum ada area minggu lalu'):
            services.save_slot(pengajuan, self.mitra, 'pit', 'minggu_lalu')

    def test_mitra_flow_with_return(self):
        pengajuan = services.request_for(self.mitra_site, self.mitra)
        with self.assertRaisesMessage(ReviewError, 'Isi semua jenis area'):
            services.ajukan(pengajuan, self.mitra)
        pengajuan = self.fill(pengajuan, self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        self.assertEqual(pengajuan.status, 'menunggu_sgi')
        with self.assertRaisesMessage(ReviewError, 'tidak bisa diubah'):  # locked while Survey BC checks
            services.save_slot(pengajuan, self.mitra, 'pit', 'tidak_ada')
        with self.assertRaisesMessage(ReviewError, 'Catatan wajib'):
            services.verifikasi(pengajuan, self.survey_bc, aksi='return')
        services.verifikasi(pengajuan, self.survey_bc, aksi='return', catatan='Pit kurang ke timur')
        self.assertEqual(pengajuan.status, 'return_mitra')
        services.save_slot(pengajuan, self.mitra, 'pit', 'upload', shp_zip([square(CAMERA[0] + 0.0005, CAMERA[1])]))
        services.ajukan(pengajuan, self.mitra)
        services.verifikasi(pengajuan, self.survey_bc, aksi='approve')
        berlaku = services.area_berlaku(self.mitra_site, W45)
        self.assertEqual((berlaku['pengajuan'], berlaku['fallback'], berlaku['catatan']), (pengajuan, False, ''))
        aksi = list(pengajuan.riwayat.values_list('aksi', flat=True))
        self.assertEqual(aksi, ['diajukan', 'dikembalikan', 'diajukan', 'disetujui'])

    def test_fallback_and_reuse_last_week(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        services.verifikasi(pengajuan, self.survey_bc, aksi='approve')
        # Week 46, nothing uploaded yet: last week's area counts, with a note.
        berlaku = services.area_berlaku(self.mitra_site, W46)
        self.assertEqual((berlaku['pengajuan'], berlaku['fallback'], berlaku['minggu_area']), (pengajuan, True, W45))
        self.assertIn('Memakai area W45 2026 (1 minggu)', berlaku['catatan'])
        self.assertIn(self.mitra_site, services.wajib_upload(W46))
        with mock.patch('areakerja.services.this_monday', return_value=W46):
            baru = services.request_for(self.mitra_site, self.mitra)
            area = services.save_slot(baru, self.mitra, 'pit', 'minggu_lalu')
        self.assertEqual((baru.minggu, area.minggu_asal, area.geom_upload), (W46, W45, pengajuan.slot('pit').geom_upload))

    def test_approved_after_the_week_still_counts_with_a_note(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        late = timezone.make_aware(datetime.datetime(2026, 11, 10, 9, 0))
        with mock.patch('areakerja.services.timezone.now', return_value=late):
            services.verifikasi(pengajuan, self.survey_bc, aksi='approve')
        berlaku = services.area_berlaku(self.mitra_site, W45)
        self.assertEqual((berlaku['pengajuan'], berlaku['catatan']), (pengajuan, 'Approved terlambat'))

    def test_berau_coal_is_approved_at_once_and_edits_are_logged(self):
        pengajuan = self.fill(services.request_for(self.bc_site, self.survey_bc), self.survey_bc, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.survey_bc)
        self.assertEqual((pengajuan.status, pengajuan.disetujui_oleh), ('approved', self.survey_bc))
        services.save_slot(pengajuan, self.survey_bc, 'pit', 'upload', shp_zip([square(CAMERA[0] + 0.0005, CAMERA[1])]))
        edit = RiwayatAreaKerja.objects.get(pengajuan=pengajuan, aksi='diubah')
        self.assertEqual((edit.user, edit.sebelum['jenis']), (self.survey_bc, 'pit'))
        self.assertIsNotNone(edit.sebelum['geom'])
        with mock.patch('areakerja.services.this_monday', return_value=W46):  # past weeks stay as they were
            with self.assertRaisesMessage(ReviewError, 'tidak bisa diubah'):
                services.save_slot(pengajuan, self.survey_bc, 'pit', 'tidak_ada')

    def test_inactive_site_stops(self):
        self.other.aktif = False
        self.other.save()
        self.assertEqual(services.area_berlaku(self.other, W45)['catatan'], 'Operasional Sudah Berhenti')
        with self.assertRaisesMessage(ReviewError, 'Operasional Sudah Berhenti'):
            services.request_for(self.other, self.mitra)

    def test_pages_and_access(self):
        self.client.force_login(self.mitra)
        page = self.client.get(reverse('areakerja:index'))
        self.assertEqual([r['mitra_site'] for r in page.context['rows']], [self.mitra_site])  # own scope only
        response = self.client.post(reverse('areakerja:buka', args=[self.mitra_site.pk]))
        pengajuan = PengajuanAreaKerja.objects.get(mitra_site=self.mitra_site)
        self.assertRedirects(response, reverse('areakerja:pengajuan', args=[pengajuan.pk]) + '?tab=pengajuan')
        self.assertEqual(self.client.post(reverse('areakerja:buka', args=[self.other.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('areakerja:buka', args=[self.bc_site.pk])).status_code, 404)
        url = reverse('areakerja:slot', args=[pengajuan.pk])
        self.client.post(url, {'jenis': 'pit', 'sumber': 'upload', 'file_shp': shp_zip([square(*CAMERA)])})
        self.assertEqual(AreaKerja.objects.get(pengajuan=pengajuan, jenis='pit').jumlah_feature, 1)
        self.assertContains(self.client.get(reverse('areakerja:pengajuan', args=[pengajuan.pk])), 'Pit')
        verify = reverse('areakerja:verifikasi', args=[pengajuan.pk])
        self.assertEqual(self.client.post(verify, {'aksi': 'approve'}).status_code, 403)
        self.client.force_login(self.survey_bc)
        self.assertEqual(len(self.client.get(reverse('areakerja:index')).context['rows']), 3)  # Survey BC sees all

    def test_areas_in_force_for_the_cctv_maps(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        url = reverse('areakerja:peta_berlaku')
        self.assertEqual(self.client.get(url).status_code, 302)  # login first
        self.client.force_login(self.mitra)
        self.assertEqual(self.client.get(url).json()['features'], [])  # not Approved yet: nothing in force
        services.verifikasi(pengajuan, self.survey_bc, aksi='approve')
        features = self.client.get(url).json()['features']
        self.assertEqual([f['properties']['jenis'] for f in features], ['pit'])  # 'tidak ada' kinds have no shape
        self.assertEqual((features[0]['properties']['site'], features[0]['properties']['fallback']), ('LMO', False))
        self.mitra_site.aktif = False
        self.mitra_site.save()
        self.assertEqual(self.client.get(url).json()['features'], [])  # stopped sites drop off the maps

    def test_list_filters_keep_across_weeks(self):
        self.client.force_login(self.survey_bc)
        page = self.client.get(reverse('areakerja:index'), {'perusahaan': self.bc.pk, 'minggu': '2026-11-12'})
        self.assertEqual([r['mitra_site'] for r in page.context['rows']], [self.bc_site])
        self.assertEqual(page.context['minggu'], W46)  # any day picks its week's Monday
        self.assertEqual(page.context['week_picker'], {'dilihat': '2026-11-09', 'berjalan': '2026-11-02',
                                                       'filter': f'perusahaan={self.bc.pk}'})
        self.assertContains(page, f'?minggu=2026-11-02&perusahaan={self.bc.pk}')  # previous week, same filter
        self.assertEqual(len(page.context['perusahaan_list']), 2)


class CoverageTests(TestCase):
    """CCTV boundaries over work areas: tercover / potensi / tidak tercover, live and per closed week."""

    databases = {'default', 'log'}
    X, Y = CAMERA

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.site = Site.objects.create(kode='LMO')
        cls.pama = Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')
        cls.lain = Perusahaan.objects.create(nama='PT Kontraktor Lain', kode='KL')
        cls.mitra_site = MitraSite.objects.create(site=cls.site, perusahaan=cls.pama)
        cls.user = User.objects.create_user('survey-bc', password='x')

    def setUp(self):
        # setUpTestData's saves left a recompute waiting on a commit that never comes in tests.
        transaction.get_connection().run_on_commit.clear()
        for target in ('areakerja.services.this_monday', 'areakerja.views.this_monday', 'areakerja.kalkulasi.this_monday'):
            patcher = mock.patch(target, return_value=W45)
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def committed(self):
        """Run what waits for the commit (live recompute, locking a waiting week): TestCase never commits.
        Then drop those callbacks, as a real commit would, so the next change schedules its own."""
        pending = transaction.get_connection().run_on_commit
        start = len(pending)
        with self.captureOnCommitCallbacks(execute=True):
            yield
        del pending[start:]

    def cctv(self, no, boundary, perusahaan=None, **fields):
        with self.committed():
            cctv = Cctv.objects.create(no_cctv=no, nama=no, site=self.site, perusahaan=perusahaan or self.pama,
                                       jenis='fixed', posisi=Point(self.X, self.Y, srid=4326), **fields)
            CctvCoverage.objects.create(cctv=cctv, minggu=W45, geom=MultiPolygon(boundary, srid=4326))
        return cctv

    def area(self, minggu=W45, pit=None, approve=True):
        with mock.patch('areakerja.services.this_monday', return_value=minggu), self.committed():
            pengajuan = services.request_for(self.mitra_site, self.user)
            services.save_slot(pengajuan, self.user, 'pit', 'upload', shp_zip([pit or square(self.X, self.Y, 0.004)]))
            for jenis in JENIS[1:]:
                services.save_slot(pengajuan, self.user, jenis, 'tidak_ada')
            services.ajukan(pengajuan, self.user)
            if approve:
                services.verifikasi(pengajuan, self.user, aksi='approve')
        return pengajuan

    def tutup(self):
        """Monday 00:00 of W46: close W45."""
        with mock.patch('areakerja.kalkulasi.this_monday', return_value=W46):
            return kalkulasi.tutup_minggu(W45)

    def live(self, mode='milik', jenis='pit'):
        return CoverageLive.objects.get(mitra_site=self.mitra_site, mode=mode, jenis=jenis)

    def test_how_a_camera_counts(self):
        cases = {('live', 'baik'): 'aktif', ('post_event', 'baik'): 'aktif', ('offline', 'baik'): 'mati',
                 ('live', 'breakdown'): 'mati', ('live', 'repair'): 'mati', ('live', 'dismantle'): None,
                 ('offline', 'take_out'): None}
        for (status, kondisi), expected in cases.items():
            with self.subTest(status=status, kondisi=kondisi):
                self.assertEqual(kalkulasi.kelompok(status, kondisi), expected)

    def test_live_coverage_in_two_modes(self):
        self.area()  # pit: a 0.004 deg square (~19.7 ha)
        s = 0.002
        self.cctv('A', square(self.X, self.Y, s))                                  # active, bottom-left quarter
        self.cctv('B', square(self.X + 0.001, self.Y, s))                          # active, overlaps A: counted once
        self.cctv('C', square(self.X, self.Y + s, s), status='offline')            # dead, top-left -> potential
        self.cctv('D', square(self.X + s, self.Y + s, s), perusahaan=self.lain)    # someone else's, top-right
        self.cctv('E', square(self.X + s, self.Y, s), kondisi='dismantle')         # removed: not counted

        milik, semua = self.live('milik'), self.live('semua')
        self.assertAlmostEqual(milik.tercover_ha + milik.potensi_ha + milik.tidak_ha, milik.luas_ha, delta=0.01)
        self.assertAlmostEqual(milik.persen_tercover, 37.5, delta=0.5)  # A u B = 3/8 of the square
        self.assertAlmostEqual(milik.persen_potensi, 25.0, delta=0.5)
        self.assertEqual(milik.jumlah_cctv, 3)
        self.assertAlmostEqual(semua.persen_tercover, 62.5, delta=0.5)  # plus the other company's camera
        self.assertEqual(self.live('milik', 'hauling').luas_ha, 0)      # 'tidak ada' kinds still get a row

        Cctv.objects.filter(no_cctv='C').update(status='live')  # .update() sends no signal
        with self.committed():
            Cctv.objects.get(no_cctv='C').save()                 # a save does: recomputed
        self.assertAlmostEqual(self.live('milik').persen_tercover, 62.5, delta=0.5)

    def test_totals_never_pass_100_percent(self):
        # A small area: 0.0653 of 0.0686 ha. Rounding the hectares first gave 0.07 / 0.0686 = 102%.
        row = CoverageLive(mode='milik', jenis='pit', luas_ha=0.0686, tercover_ha=0.0653, potensi_ha=0.0, tidak_ha=0.0033)
        total = kalkulasi.ringkasan([row])
        self.assertEqual(total['persen']['tercover'], 95.2)
        self.assertEqual((total['luas'], total['tercover']), (0.0686, 0.0653))

    def test_closed_week_is_locked(self):
        self.area()
        self.cctv('A', square(self.X, self.Y, 0.002))
        close_week(W45)
        self.assertEqual(self.tutup(), (1, 0))
        snap = SnapshotCoverage.objects.get(minggu=W45, mitra_site=self.mitra_site)
        pit = snap.hasil.get(mode='milik', jenis='pit')
        self.assertEqual((snap.status, snap.fallback, snap.pilihan_slot['pit']['sumber']), ('terkunci', False, 'upload'))
        self.assertAlmostEqual(pit.persen_tercover, 25.0, delta=0.5)
        self.cctv('B', square(self.X + 0.002, self.Y, 0.002))  # later changes do not touch a locked week
        self.assertEqual(self.tutup(), (0, 0))
        self.assertAlmostEqual(snap.hasil.get(mode='milik', jenis='pit').persen_tercover, 25.0, delta=0.5)

    def test_week_waits_for_a_late_approval(self):
        w44 = W45 - datetime.timedelta(days=7)
        self.area(minggu=w44)                                                     # last week's area
        pengajuan = self.area(pit=square(self.X, self.Y, 0.002), approve=False)  # this week's, not yet checked
        self.cctv('A', square(self.X, self.Y, 0.002))
        close_week(W45)
        self.assertEqual(self.tutup(), (0, 1))
        snap = SnapshotCoverage.objects.get(minggu=W45, mitra_site=self.mitra_site)
        self.assertEqual((snap.status, snap.hasil.count()), ('menunggu', 0))

        late = timezone.make_aware(datetime.datetime(2026, 11, 11, 9, 0))
        with mock.patch('areakerja.services.timezone.now', return_value=late), self.committed():
            services.verifikasi(pengajuan, self.user, aksi='approve')  # locks it right away
        snap.refresh_from_db()
        self.assertEqual((snap.status, snap.minggu_area, snap.catatan), ('terkunci', W45, 'Approved terlambat'))
        self.assertAlmostEqual(snap.hasil.get(mode='milik', jenis='pit').persen_tercover, 100.0, delta=0.5)

    def test_wait_ends_with_the_fallback(self):
        w44 = W45 - datetime.timedelta(days=7)
        self.area(minggu=w44)
        self.area(approve=False)
        close_week(W45)
        self.tutup()
        self.assertEqual(kalkulasi.selesaikan_menunggu(today=W45 + datetime.timedelta(days=13)), 0)  # still waiting
        self.assertEqual(kalkulasi.selesaikan_menunggu(today=W45 + datetime.timedelta(days=14)), 1)
        snap = SnapshotCoverage.objects.get(minggu=W45, mitra_site=self.mitra_site)
        self.assertEqual((snap.status, snap.fallback, snap.minggu_area), ('terkunci', True, w44))
        self.assertIn('Fallback', snap.catatan)
