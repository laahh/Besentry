import datetime
import json
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


def setujui_semua(pengajuan, user):
    """Verifikasi BC: SGI, Mining and Safety BC each approve; the third makes it Approved."""
    for departemen in ('sgi', 'mining', 'safety'):
        services.verifikasi(pengajuan, user, departemen=departemen, aksi='setujui')


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
        self.assertEqual(JENIS, ['pit', 'hauling', 'infra_tambang', 'infra_non_tambang', 'high_risk'])


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

    def fill(self, pengajuan, user, pit=None, hauling=None, high_risk=None):
        """Selected kinds are uploaded; every other weekly slot is explicitly marked absent."""
        for jenis, polygons in (('pit', pit), ('hauling', hauling), ('high_risk', high_risk)):
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

    def test_high_risk_keeps_overlap_and_is_not_clipped(self):
        pengajuan = services.request_for(self.mitra_site, self.mitra)
        overlap = square(*CAMERA)
        pengajuan = self.fill(pengajuan, self.mitra, pit=[overlap], high_risk=[overlap])
        pit, high_risk = pengajuan.slot('pit'), pengajuan.slot('high_risk')

        self.assertAlmostEqual(high_risk.luas_ha, pit.luas_ha, delta=0.01)
        self.assertAlmostEqual(high_risk.geom.intersection(pit.geom).area, pit.geom.area, delta=1e-12)
        self.assertEqual(high_risk.dipotong_ha, 0)

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
        with self.assertRaisesMessage(ReviewError, 'tidak bisa diubah'):  # locked during Verifikasi BC
            services.save_slot(pengajuan, self.mitra, 'pit', 'tidak_ada')
        services.verifikasi(pengajuan, self.survey_bc, departemen='sgi', aksi='setujui')
        with self.assertRaisesMessage(ReviewError, 'Catatan wajib'):
            services.verifikasi(pengajuan, self.survey_bc, departemen='mining', aksi='kembalikan')
        services.verifikasi(pengajuan, self.survey_bc, departemen='mining', aksi='kembalikan', catatan='Pit kurang ke timur')
        self.assertEqual((pengajuan.status, pengajuan.status_label), ('return_mitra', 'Return to Mitra'))
        services.save_slot(pengajuan, self.mitra, 'pit', 'upload', shp_zip([square(CAMERA[0] + 0.0005, CAMERA[1])]))
        services.ajukan(pengajuan, self.mitra)
        setujui_semua(pengajuan, self.survey_bc)
        berlaku = services.area_berlaku(self.mitra_site, W45)
        self.assertEqual((berlaku['pengajuan'], berlaku['fallback'], berlaku['catatan']), (pengajuan, False, ''))
        aksi = list(pengajuan.riwayat.values_list('aksi', flat=True))
        self.assertEqual(aksi, ['diajukan', 'verifikasi', 'dikembalikan', 'diajukan',
                                'verifikasi', 'verifikasi', 'verifikasi', 'disetujui'])

    def test_verifikasi_needs_all_three_departments(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        services.verifikasi(pengajuan, self.survey_bc, departemen='sgi', aksi='setujui')
        services.verifikasi(pengajuan, self.survey_bc, departemen='safety', aksi='setujui')
        self.assertEqual(pengajuan.status, 'menunggu_sgi')  # Mining still to approve
        with self.assertRaisesMessage(ReviewError, 'sudah menyetujui'):
            services.verifikasi(pengajuan, self.survey_bc, departemen='sgi', aksi='setujui')
        services.verifikasi(pengajuan, self.survey_bc, departemen='mining', aksi='kembalikan', catatan='Hauling belum ada')
        services.save_slot(pengajuan, self.mitra, 'hauling', 'upload', shp_zip([square(CAMERA[0] + 0.002, CAMERA[1])]))
        services.ajukan(pengajuan, self.mitra)
        self.assertEqual(pengajuan.verifikasi.count(), 0)  # sent again: every department checks afresh
        setujui_semua(pengajuan, self.survey_bc)
        self.assertEqual(pengajuan.status, 'approved')
        with self.assertRaisesMessage(ReviewError, 'tidak bisa diubah'):  # nothing changes once Approved
            services.save_slot(pengajuan, self.mitra, 'pit', 'tidak_ada')

    def test_fallback_and_reuse_last_week(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        setujui_semua(pengajuan, self.survey_bc)
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
            setujui_semua(pengajuan, self.survey_bc)
        berlaku = services.area_berlaku(self.mitra_site, W45)
        self.assertEqual((berlaku['pengajuan'], berlaku['catatan']), (pengajuan, 'Approved terlambat'))

    def test_berau_coal_goes_through_verifikasi_too(self):
        pengajuan = self.fill(services.request_for(self.bc_site, self.survey_bc), self.survey_bc, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.survey_bc)
        self.assertEqual(pengajuan.status, 'menunggu_sgi')  # no longer in force at once
        services.verifikasi(pengajuan, self.survey_bc, departemen='safety', aksi='kembalikan', catatan='Batas pit lama')
        self.assertEqual(pengajuan.status_label, 'Return to Survey BC')
        services.ajukan(pengajuan, self.survey_bc)
        setujui_semua(pengajuan, self.survey_bc)
        self.assertEqual(pengajuan.status, 'approved')
        self.client.force_login(self.survey_bc)
        page = self.client.get(reverse('areakerja:pengajuan', args=[pengajuan.pk]))
        self.assertEqual([key for key, *_ in page.context['tabs']], ['pengajuan', 'sgi', 'selesai'])
        # Approved: only Sah & berlaku opens, whatever the link asks for.
        self.assertEqual([link for *_, link in page.context['tabs']], [False, False, True])
        page = self.client.get(reverse('areakerja:pengajuan', args=[pengajuan.pk]), {'tab': 'pengajuan'})
        self.assertEqual(page.context['tab'], 'selesai')
        self.assertNotContains(page, '?tab=sgi')

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
        self.assertEqual(self.client.post(verify, {'departemen': 'sgi', 'aksi': 'setujui'}).status_code, 403)
        self.client.force_login(self.survey_bc)
        self.assertEqual(len(self.client.get(reverse('areakerja:index')).context['rows']), 3)  # Survey BC sees all

    def test_areas_in_force_for_the_cctv_maps(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        url = reverse('areakerja:peta_berlaku')
        self.assertEqual(self.client.get(url).status_code, 302)  # login first
        self.client.force_login(self.mitra)
        self.assertEqual(self.client.get(url).json()['features'], [])  # not Approved yet: nothing in force
        setujui_semua(pengajuan, self.survey_bc)
        features = self.client.get(url).json()['features']
        self.assertEqual([f['properties']['jenis'] for f in features], ['pit'])  # 'tidak ada' kinds have no shape
        self.assertEqual((features[0]['properties']['site'], features[0]['properties']['fallback']), ('LMO', False))
        self.mitra_site.aktif = False
        self.mitra_site.save()
        self.assertEqual(self.client.get(url).json()['features'], [])  # stopped sites drop off the maps

    def test_each_department_approves_only_for_itself(self):
        User = get_user_model()
        mining = User.objects.create_user('mining', password='x')
        mining.groups.add(Group.objects.get(name=roles.MINING))
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        self.client.force_login(mining)
        verify = reverse('areakerja:verifikasi', args=[pengajuan.pk])
        self.assertEqual(self.client.post(verify, {'departemen': 'sgi', 'aksi': 'setujui'}).status_code, 403)
        self.client.post(verify, {'departemen': 'mining', 'aksi': 'setujui'})
        self.assertEqual(list(pengajuan.verifikasi.values_list('departemen', 'user')), [('mining', mining.pk)])
        page = self.client.get(reverse('areakerja:pengajuan', args=[pengajuan.pk]))
        self.assertEqual(page.context['departemen_saya'], ['mining'])

    def test_map_notes_stay_until_solved(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        add = reverse('areakerja:anotasi_tambah', args=[pengajuan.pk])
        self.client.force_login(self.survey_bc)
        note = {'departemen': 'sgi', 'lon': CAMERA[0] + 0.0005, 'lat': CAMERA[1] + 0.0005, 'teks': 'Ramp belum masuk area'}
        self.assertEqual(self.client.post(add, note).status_code, 400)  # only during Verifikasi BC
        services.ajukan(pengajuan, self.mitra)
        self.assertEqual(self.client.post(add, {**note, 'departemen': 'safety'}).status_code, 403)  # not their department
        made = self.client.post(add, note).json()
        self.assertEqual((made['departemen'], made['milik_saya'], made['selesai']), ('SGI', True, False))
        services.verifikasi(pengajuan, self.survey_bc, departemen='sgi', aksi='kembalikan', catatan='Lihat anotasi')

        # The uploader fixes the area; the note is still there, and is ticked solved.
        services.save_slot(pengajuan, self.mitra, 'pit', 'upload', shp_zip([square(*CAMERA, size=0.0012)]))
        self.client.force_login(self.mitra)
        page = self.client.get(reverse('areakerja:pengajuan', args=[pengajuan.pk]))
        self.assertEqual([a.teks for a in page.context['anotasi']], ['Ramp belum masuk area'])
        tandai = reverse('areakerja:anotasi_tandai', args=[pengajuan.pk, made['id']])
        self.client.post(tandai, {'selesai': '1'})
        anotasi = pengajuan.anotasi.get()
        self.assertEqual((anotasi.selesai, anotasi.selesai_oleh), (True, self.mitra))
        services.ajukan(pengajuan, self.mitra)
        self.client.force_login(self.survey_bc)
        hapus = reverse('areakerja:anotasi_hapus', args=[pengajuan.pk, made['id']])
        self.assertEqual(self.client.post(hapus).status_code, 200)
        self.assertEqual(pengajuan.anotasi.count(), 0)

    def test_line_and_area_notes_carry_their_size(self):
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        services.ajukan(pengajuan, self.mitra)
        x, y = CAMERA
        garis = json.dumps({'type': 'LineString', 'coordinates': [[x, y], [x + 0.001, y]]})          # ~111 m
        area = json.dumps({'type': 'Polygon', 'coordinates': [[[x, y], [x + 0.001, y], [x + 0.001, y + 0.001],
                                                               [x, y + 0.001], [x, y]]]})         # ~1.2 ha
        line = services.tambah_anotasi(pengajuan, self.survey_bc, 'sgi', 'Jalan belum masuk', geometri=garis)
        self.assertEqual(line.bentuk, 'garis')
        self.assertAlmostEqual(line.panjang_m, 111, delta=2)
        self.assertAlmostEqual(line.posisi.x, x + 0.0005, places=6)  # the number sits mid-line
        poly = services.tambah_anotasi(pengajuan, self.survey_bc, 'sgi', 'Disposal baru', geometri=area)
        self.assertEqual((poly.bentuk, round(poly.luas_ha, 1)), ('area', 1.2))
        self.assertTrue(poly.geom.contains(poly.posisi))
        self.assertEqual((line.ukuran, poly.ukuran), ('111 m', '1,23 ha'))
        bad = (
            ({'type': 'LineString', 'coordinates': [[x, y]]}, 'minimal 2'),
            ({'type': 'Polygon', 'coordinates': [[[x, y], [x + 0.001, y + 0.001], [x + 0.001, y], [x, y + 0.001], [x, y]]]},
             'saling memotong'),
            ({'type': 'LineString', 'coordinates': [[110.0, -5.0], [110.001, -5.0]]}, 'luar batas'),
        )
        for geometry, message in bad:
            with self.subTest(message), self.assertRaisesMessage(ReviewError, message):
                services.tambah_anotasi(pengajuan, self.survey_bc, 'sgi', 'x', geometri=json.dumps(geometry))
        self.client.force_login(self.survey_bc)
        made = self.client.post(reverse('areakerja:anotasi_tambah', args=[pengajuan.pk]),
                                {'departemen': 'sgi', 'geometri': garis, 'teks': 'Lewat halaman'}).json()
        self.assertEqual((made['bentuk'], made['ukuran'], made['geom']['type']), ('garis', '111 m', 'LineString'))

    def test_cctv_around_the_areas(self):
        from cctv.models import Cctv, CctvCoverage
        pengajuan = self.fill(services.request_for(self.mitra_site, self.mitra), self.mitra, pit=[square(*CAMERA)])
        own = Cctv.objects.create(no_cctv='LMO-PM-1', nama='A', site=self.site, perusahaan=self.pama, jenis='fixed',
                                  posisi=Point(*CAMERA, srid=4326))
        CctvCoverage.objects.create(cctv=own, minggu=W45, geom=MultiPolygon(square(*CAMERA, size=0.0005), srid=4326))
        Cctv.objects.create(no_cctv='LMO-BC-1', nama='B', site=self.site, perusahaan=self.bc, jenis='fixed',
                            posisi=Point(CAMERA[0] + 0.001, CAMERA[1], srid=4326), kondisi='repair')
        Cctv.objects.create(no_cctv='SMO-PM-1', nama='Jauh', site=self.site, perusahaan=self.pama, jenis='fixed',
                            posisi=Point(CAMERA[0] + 0.05, CAMERA[1], srid=4326))
        self.client.force_login(self.survey_bc)
        data = self.client.get(reverse('areakerja:cctv_sekitar', args=[pengajuan.pk])).json()
        titik = {f['properties']['no']: f['properties'] for f in data['titik']['features']}
        self.assertEqual(set(titik), {'LMO-PM-1', 'LMO-BC-1'})  # the far one is left out
        self.assertEqual((titik['LMO-PM-1']['milik'], titik['LMO-BC-1']['milik']), (True, False))
        self.assertEqual(titik['LMO-BC-1']['kelompok'], 'mati')
        self.assertEqual([f['properties']['no'] for f in data['coverage']['features']], ['LMO-PM-1'])

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
        jenis = fields.pop('jenis', 'fixed')
        with self.committed():
            cctv = Cctv.objects.create(no_cctv=no, nama=no, site=self.site, perusahaan=perusahaan or self.pama,
                                       jenis=jenis, posisi=Point(self.X, self.Y, srid=4326), **fields)
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
                setujui_semua(pengajuan, self.user)
        return pengajuan

    def tutup(self):
        """Monday 00:00 of W46: close W45."""
        with mock.patch('areakerja.kalkulasi.this_monday', return_value=W46):
            return kalkulasi.tutup_minggu(W45)

    def live(self, mode='milik', jenis='pit'):
        return CoverageLive.objects.get(mitra_site=self.mitra_site, mode=mode, jenis=jenis, dengan_ptz=False)

    def test_ptz_is_optional_and_excluded_by_default(self):
        self.area()
        self.cctv('FIXED', square(self.X, self.Y, 0.002), jenis='fixed')
        self.cctv('PTZ', square(self.X + 0.002, self.Y, 0.002), jenis='ptz')
        self.cctv('HYBRID', square(self.X, self.Y + 0.002, 0.002), jenis='ptz_fixed')

        tanpa = self.live()
        dengan = CoverageLive.objects.get(
            mitra_site=self.mitra_site, mode='milik', jenis='pit', dengan_ptz=True,
        )
        self.assertAlmostEqual(tanpa.persen_tercover, 25.0, delta=0.5)
        self.assertAlmostEqual(dengan.persen_tercover, 75.0, delta=0.5)
        self.assertEqual((tanpa.jumlah_cctv, dengan.jumlah_cctv), (1, 3))

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

    def test_high_risk_coverage_is_calculated_separately(self):
        with self.committed():
            pengajuan = services.request_for(self.mitra_site, self.user)
            boundary = square(self.X, self.Y, 0.004)
            services.save_slot(pengajuan, self.user, 'pit', 'upload', shp_zip([boundary]))
            services.save_slot(pengajuan, self.user, 'high_risk', 'upload', shp_zip([boundary]))
            for jenis in ('hauling', 'infra_tambang', 'infra_non_tambang'):
                services.save_slot(pengajuan, self.user, jenis, 'tidak_ada')
            services.ajukan(pengajuan, self.user)
            setujui_semua(pengajuan, self.user)
        self.cctv('A', square(self.X, self.Y, 0.002))

        rows = list(CoverageLive.objects.filter(mitra_site=self.mitra_site, dengan_ptz=False))
        main = kalkulasi.ringkasan(rows)
        high_risk = kalkulasi.ringkasan(rows, jenis=[JenisArea.HIGH_RISK])
        self.assertAlmostEqual(main['luas'], high_risk['luas'], delta=0.01)
        self.assertAlmostEqual(main['persen']['tercover'], 25.0, delta=0.5)
        self.assertAlmostEqual(high_risk['persen']['tercover'], 25.0, delta=0.5)

    def test_closed_week_is_locked(self):
        self.area()
        self.cctv('A', square(self.X, self.Y, 0.002))
        close_week(W45)
        self.assertEqual(self.tutup(), (1, 0))
        snap = SnapshotCoverage.objects.get(minggu=W45, mitra_site=self.mitra_site)
        pit = snap.hasil.get(mode='milik', jenis='pit', dengan_ptz=False)
        self.assertEqual((snap.status, snap.fallback, snap.pilihan_slot['pit']['sumber']), ('terkunci', False, 'upload'))
        self.assertAlmostEqual(pit.persen_tercover, 25.0, delta=0.5)
        self.cctv('B', square(self.X + 0.002, self.Y, 0.002))  # later changes do not touch a locked week
        self.assertEqual(self.tutup(), (0, 0))
        self.assertAlmostEqual(snap.hasil.get(mode='milik', jenis='pit', dengan_ptz=False).persen_tercover, 25.0, delta=0.5)

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
            setujui_semua(pengajuan, self.user)  # the third approval locks it right away
        snap.refresh_from_db()
        self.assertEqual((snap.status, snap.minggu_area, snap.catatan), ('terkunci', W45, 'Approved terlambat'))
        self.assertAlmostEqual(snap.hasil.get(mode='milik', jenis='pit', dengan_ptz=False).persen_tercover, 100.0, delta=0.5)

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
