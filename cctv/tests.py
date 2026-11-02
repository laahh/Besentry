import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point
from django.db import connection, connections
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from core.models import DetailLokasi, Lokasi, Perusahaan, Site
from navigation import roles

from navigation.menu import build_menu

from . import importer
from .access import get_scope
from .models import CakupanCctv, Cctv, CctvAkses, CctvCoverage, CctvSpec

BC_HEADERS = [
    'MC-WORKSHOP-FAD-1', 'Perusahaan', 'Kategori', 'No. CCTV',
    'Nama CCTV (Code Area - FreeText Penamaan CCTV_Fungsi CCTV)', 'No. Channel', 'Group NVR', 'Fungsi CCTV',
    'Bentuk Instalasi CCTV', 'Jenis', 'Tipe CCTV', 'Radius Pengawasan (Spesifikasi CCTV)',
    'Jenis Spesifikasi Zoom', 'Lokasi Pemasangan', 'Control Room', 'Status', 'Kondisi', 'Longitude', 'Latitude',
    'id lokasi', 'Coverage Lokasi', 'id detail lokasi', 'Coverage Detail Lokasi', 'Kategori Area Tercapture',
    'Kategori Aktivitas Tercapture', 'IP CCTV', 'IP NVR', 'User Name', 'Password', 'Connected', 'Mirrored',
    'Fitur Auto Alert (Yes/No)', 'Keterangan', 'Verifikasi By Petugas OCR', 'Bulan Update', 'Tahun Update',
    'Name PRTG',
]
BC_ROW = [
    'LMO', 'PT Berau Coal', 'CCTV Support', 'LMO-BC-0001', 'CR-01 DUMPING', 'D27', 'nvr-lmo-office-1\n',
    'Fixed View', 'Statis', 'FIX', 'HIKVision', 200, '', 'CPP', 'Office BC LMO', 'Live View', 'Baik',
    117.6041883, 2.216422084, 114153, 'Coal Processing Plant (CPP)', 1172174953.0, 'CR/HP/CV 01',
    'Area Non Kritis', 'Aktivitas Non Kritis', '10.1.202.87', '10.1.97.131', '', '', 'Yes', 'Yes', 'Yes', '',
    '', 8, 2025, 'done',
]
MITRA_HEADERS = [
    'Site', 'Perusahaan', 'Kategori', 'No. CCTV', 'Nama CCTV (Code Area - FreeText Penamaan CCTV_Fungsi CCTV)',
    'Fungsi CCTV', 'Bentuk Instalasi CCTV', 'Jenis', 'Tipe CCTV', 'Radius Pengawasan (Spesifikasi CCTV)',
    'Jenis Spesifikasi Zoom', 'Lokasi Pemasangan', 'Control Room', 'Status', 'Kondisi', 'Longitude', 'Latitude',
    'id lokasi', 'Coverage Lokasi', 'id detail lokasi', 'Coverage Detail Lokasi', 'Kategori Area Tercapture',
    'Kategori Aktivitas Tercapture', 'Link Akses', 'User Name', 'Password', 'Connected', 'Mirrored',
    'Fitur Auto Alert (Yes/No)', 'Keterangan', 'Verifikasi By Petugas OCR', 'Bulan Update', 'Tahun Update',
]
MITRA_ROW = [
    'EKSPLORASI', 'PT  Duta Borneo Mining', 'CCTV Portable', 'DBM-CCTV-001', 'DBM_PNN_DM01_01', 'Portable (IKK)',
    'Dinamis', 'PTZ', 'G-Lenz', 100, 'GWIP-2300 (4G)', 'DBM-DM-001 ', '', 'Live View', 'Baik', '', '',
    1172178432, 'EKSPLORASI PUNAN', 1172178728, 'AREA PENGEBORAN - RIG DM 001', 'Area Kritis',
    'Aktivitas Highrisk', 'https://play.google.com/x', 'drilling@gmail.com', 'rahasia\nverif code: 123',
    'YES', 'YES', 'N/A', '', '', 5, 2026,
]


def workbook(*sheets):
    """An in-memory .xlsx, read back the way the command reads it."""
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        sheet = wb.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return load_workbook(buffer, read_only=True, data_only=True)


def sample_workbook():
    return workbook(
        ('BC LMO', [BC_HEADERS, BC_ROW]),
        ('Mitra Eksplorasi', [MITRA_HEADERS, MITRA_ROW, [''] * len(MITRA_HEADERS)]),
        ('List CCTV', [MITRA_HEADERS, ['HO', 'CV X', 'CCTV Support', 'HO-X-1']]),
        ('Resume', [['Site', 'Jumlah'], ['LMO', 1]]),
    )


class CleaningTests(TestCase):
    def test_clean_collapses_spaces_and_whole_floats(self):
        self.assertEqual(importer.clean('  PT  Pamapersada \n Nusantara '), 'PT Pamapersada Nusantara')
        self.assertEqual(importer.clean(1172174953.0), '1172174953')
        self.assertEqual(importer.clean(None), '')

    def test_kekritisan(self):
        self.assertEqual(importer.kekritisan('Area Non Kritis'), 'non_kritis')
        self.assertEqual(importer.kekritisan('Aktivitas Kritis'), 'kritis')
        self.assertEqual(importer.kekritisan('Aktivitas Highrisk'), 'highrisk')
        self.assertEqual(importer.kekritisan(''), '')

    def test_header_map_uses_column_a_as_site_even_when_broken(self):
        mapping = importer.header_map(BC_HEADERS)
        self.assertEqual(mapping[0], 'site')
        self.assertEqual(mapping[BC_HEADERS.index('Kategori')], 'kategori')
        self.assertEqual(mapping[BC_HEADERS.index('Kategori Area Tercapture')], 'kategori_area')
        self.assertEqual(mapping[BC_HEADERS.index('Jenis Spesifikasi Zoom')], 'model')
        self.assertEqual(mapping[BC_HEADERS.index('Jenis')], 'jenis')

    def test_invalid_coordinates_are_dropped(self):
        self.assertIsNone(importer.to_point('', ''))
        self.assertIsNone(importer.to_point(200, 2))
        self.assertEqual(importer.to_point('117,5', '2.1').coords, (117.5, 2.1))


class ImportTests(TestCase):
    databases = {'default', 'log'}

    def test_imports_site_tabs_only(self):
        report, imported = importer.import_workbook(sample_workbook())
        self.assertEqual(imported, ['BC LMO', 'Mitra Eksplorasi'])
        self.assertEqual((report.created, report.updated, report.skipped), (2, 0, 1))
        self.assertFalse(Cctv.objects.filter(no_cctv='HO-X-1').exists())

    def test_normalises_values(self):
        importer.import_workbook(sample_workbook())
        bc = Cctv.objects.select_related('site', 'perusahaan', 'control_room', 'spec', 'akses').get(
            no_cctv='LMO-BC-0001')
        self.assertEqual(bc.site.kode, 'LMO')
        self.assertEqual(bc.perusahaan.kode, 'BC')
        self.assertEqual((bc.jenis, bc.fungsi, bc.kategori), ('fixed', 'fixed', 'support'))
        self.assertEqual((bc.kategori_area, bc.status, bc.kondisi), ('non_kritis', 'live', 'baik'))
        self.assertEqual(bc.control_room.nama, 'Office BC LMO')
        self.assertTrue(bc.connected and bc.auto_alert)
        self.assertEqual(bc.periode_update.isoformat(), '2025-08-01')
        self.assertAlmostEqual(bc.posisi.x, 117.6041883)
        self.assertEqual((bc.lokasi_id, bc.detail_lokasi_id), (114153, 1172174953))
        self.assertEqual((bc.spec.brand, bc.spec.radius_m), ('Hikvision', 200))
        self.assertEqual((bc.akses.nvr, bc.akses.channel, bc.akses.prtg_name), ('nvr-lmo-office-1', 'D27', 'done'))

        mitra = Cctv.objects.select_related('perusahaan', 'akses').get(no_cctv='DBM-CCTV-001')
        self.assertEqual(mitra.perusahaan.nama, 'PT Duta Borneo Mining')
        self.assertEqual((mitra.kategori_aktivitas, mitra.fungsi), ('highrisk', 'portable'))
        self.assertIsNone(mitra.posisi)
        self.assertFalse(mitra.auto_alert)
        self.assertEqual(mitra.akses.password, 'rahasia\nverif code: 123')

    def test_password_is_encrypted_at_rest(self):
        importer.import_workbook(sample_workbook())
        with connection.cursor() as cursor:
            cursor.execute('SELECT password FROM cctv_cctvakses WHERE password <> %s', [''])
            stored = cursor.fetchone()[0]
        self.assertNotIn('rahasia', stored)

    def test_reimport_updates_and_skips_unchanged_rows(self):
        importer.import_workbook(sample_workbook())
        history_rows = Cctv.history.count()

        report, _ = importer.import_workbook(sample_workbook())
        self.assertEqual((report.created, report.updated), (0, 2))
        self.assertEqual(Cctv.history.count(), history_rows)

        changed = BC_ROW.copy()
        changed[BC_HEADERS.index('Kondisi')] = 'Breakdown'
        importer.import_workbook(workbook(('BC LMO', [BC_HEADERS, changed])))
        latest = Cctv.history.filter(no_cctv='LMO-BC-0001').latest('history_date')
        self.assertEqual((latest.kondisi, latest.history_change_reason), ('breakdown', importer.CHANGE_REASON))

    def test_unknown_choice_is_reported(self):
        row = BC_ROW.copy()
        row[BC_HEADERS.index('Jenis')] = 'RID'
        report, _ = importer.import_workbook(workbook(('BC LMO', [BC_HEADERS, row])))
        self.assertEqual(Cctv.objects.get().jenis, '')
        self.assertIn('Jenis "RID" tidak dikenal', report.warnings[0])

    def test_new_lokasi_ids_continue_after_imported_ones(self):
        importer.import_workbook(sample_workbook())
        lokasi = Lokasi.objects.create(nama='Baru')
        self.assertGreater(lokasi.pk, 1172178432)


class LogDatabaseTests(TestCase):
    databases = {'default', 'log'}

    def test_history_is_written_to_the_log_database(self):
        cctv = Cctv.objects.create(
            no_cctv='X-1', nama='X', site=Site.objects.create(kode='LMO'),
            perusahaan=Perusahaan.objects.create(nama='PT X'),
        )
        with connections['log'].cursor() as cursor:
            cursor.execute('SELECT no_cctv FROM cctv_log_historicalcctv WHERE id = %s', [cctv.pk])
            self.assertEqual(cursor.fetchone(), ('X-1',))
        self.assertNotIn('cctv_log_historicalcctv', connection.introspection.table_names())

    def test_deleting_a_user_keeps_their_history(self):
        user = get_user_model().objects.create_user('lama', password='x')
        site = Site.objects.create(kode='LMO')
        cctv = Cctv(no_cctv='X-1', nama='X', site=site, perusahaan=Perusahaan.objects.create(nama='PT X'))
        cctv._history_user = user
        cctv.save()

        user_id = user.pk
        user.delete()  # used to fail: the cascade looked for the history table in `default`
        self.assertEqual(cctv.history.get().history_user_id, user_id)
        self.assertIsNone(cctv.history.get().history_user)


class ViewTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user('admin', password='x')
        cls.admin.groups.add(Group.objects.get(name=roles.ADMIN_HAZARD_MOTION))
        cls.it = User.objects.create_user('it', password='x')
        cls.it.groups.add(Group.objects.get(name=roles.IT_PAMA))
        cls.site = Site.objects.create(kode='LMO')
        cls.other_site = Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal', kode='BC')
        cls.lokasi = Lokasi.objects.create(id=114153, nama='CPP')
        cls.detail = DetailLokasi.objects.create(id=1172174953, lokasi=cls.lokasi, nama='CR 01')

    def make(self, no, site=None, **extra):
        return Cctv.objects.create(
            no_cctv=no, nama=f'Kamera {no}', site=site or self.site, perusahaan=self.perusahaan, **extra,
        )

    def form_data(self, **extra):
        data = {
            'no_cctv': 'LMO-BC-0009', 'nama': 'Kamera baru', 'site': self.site.pk, 'perusahaan': self.perusahaan.pk,
            'status': 'live', 'kondisi': 'baik', 'lokasi': self.lokasi.pk, 'detail_lokasi': self.detail.pk,
            'longitude': '117.6', 'latitude': '2.2', 'spec-brand': 'Hikvision', 'spec-radius_m': '50',
        }
        data.update(extra)
        return data

    def test_requires_cctv_role(self):
        self.client.force_login(get_user_model().objects.create_user('biasa', password='x'))
        self.assertEqual(self.client.get(reverse('cctv:index')).status_code, 403)

    def test_list_filters_and_counts(self):
        self.make('LMO-BC-0001')
        self.make('LMO-BC-0002', kondisi='breakdown')
        self.make('SMO-BC-0001', site=self.other_site)
        self.client.force_login(self.admin)

        response = self.client.get(reverse('cctv:index'), {'site': self.site.pk})
        self.assertEqual(response.context['stats']['total'], 2)
        self.assertEqual(response.context['stats']['bermasalah'], 1)
        self.assertContains(response, 'LMO-BC-0002')
        self.assertNotContains(response, 'SMO-BC-0001')

        response = self.client.get(reverse('cctv:index'), {'q': 'smo-bc', 'kondisi': 'tidak-ada'})
        self.assertEqual(response.context['stats']['total'], 1)
        self.assertNotIn('kondisi', response.context['selected'])

    def test_list_query_count_does_not_grow_with_rows(self):
        self.client.force_login(self.admin)
        self.make('A-1')
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse('cctv:index'))
        for number in range(2, 30):
            self.make(f'A-{number}', control_room=None)
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse('cctv:index'))
        self.assertEqual(len(few), len(many))

    def test_geojson_and_export(self):
        self.make('LMO-BC-0001', posisi=Point(117.6, 2.2, srid=4326))
        self.make('LMO-BC-0002')
        self.client.force_login(self.admin)

        features = self.client.get(reverse('cctv:geojson')).json()['features']
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]['geometry']['coordinates'], [117.6, 2.2])

        response = self.client.get(reverse('cctv:export'), {'site': self.site.pk})
        body = b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertEqual(len(body.strip().splitlines()), 3)
        self.assertIn('LMO-BC-0001', body)
        self.assertIn('Live View,Baik,117.6,2.2,No', body)

    def test_create_saves_spec_and_history_with_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('cctv:create'), self.form_data())
        cctv = Cctv.objects.get(no_cctv='LMO-BC-0009')
        self.assertRedirects(response, reverse('cctv:detail', args=[cctv.pk]))
        self.assertEqual(cctv.posisi.coords, (117.6, 2.2))
        self.assertEqual(cctv.spec.brand, 'Hikvision')
        self.assertFalse(CctvAkses.objects.exists())  # admin has no akses form
        self.assertEqual(cctv.history.get().history_user_id, self.admin.pk)

    def test_edit_form_renders_periode_as_iso(self):
        # <input type=date> shows nothing for a locale-formatted value, and saving would clear it.
        cctv = self.make('LMO-BC-0001', periode_update='2025-08-01')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('cctv:update', args=[cctv.pk])), 'value="2025-08-01"')

    def test_detail_lokasi_must_belong_to_lokasi(self):
        other = DetailLokasi.objects.create(lokasi=Lokasi.objects.create(nama='Lain'), nama='Lain')
        self.client.force_login(self.admin)
        response = self.client.post(reverse('cctv:create'), self.form_data(detail_lokasi=other.pk))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Cctv.objects.exists())

    def test_detail_shows_history_of_changes(self):
        self.client.force_login(self.admin)
        cctv = self.make('LMO-BC-0001')
        self.client.post(reverse('cctv:update', args=[cctv.pk]), self.form_data(no_cctv='LMO-BC-0001',
                                                                                 kondisi='breakdown'))
        response = self.client.get(reverse('cctv:detail', args=[cctv.pk]))
        riwayat = response.context['riwayat']
        self.assertEqual(riwayat[-1]['type'], 'Dibuat')
        kondisi = [c for e in riwayat for c in e['changes'] if c['field'] == 'kondisi']
        self.assertEqual((kondisi[0]['old'], kondisi[0]['new']), ('Baik', 'Breakdown'))
        self.assertContains(response, 'admin')

    def test_akses_is_it_only(self):
        cctv = self.make('LMO-BC-0001')
        CctvAkses.objects.create(cctv=cctv, ip_cctv='10.1.1.1', password='rahasia')
        url = reverse('cctv:password', args=[cctv.pk])

        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('cctv:detail', args=[cctv.pk])), '10.1.1.1')
        self.assertEqual(self.client.post(url).status_code, 403)

        self.client.force_login(self.it)
        detail = self.client.get(reverse('cctv:detail', args=[cctv.pk]))
        self.assertContains(detail, '10.1.1.1')
        self.assertNotContains(detail, 'rahasia')
        self.assertEqual(self.client.post(url).json(), {'password': 'rahasia'})
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_blank_password_keeps_the_stored_one(self):
        cctv = self.make('LMO-BC-0001')
        CctvAkses.objects.create(cctv=cctv, password='lama')
        self.client.force_login(self.it)

        self.client.post(reverse('cctv:update', args=[cctv.pk]),
                         self.form_data(no_cctv='LMO-BC-0001', **{'akses-username': 'u'}))
        akses = CctvAkses.objects.get()
        self.assertEqual((akses.username, akses.password), ('u', 'lama'))

        self.client.post(reverse('cctv:update', args=[cctv.pk]),
                         self.form_data(no_cctv='LMO-BC-0001', **{'akses-password_baru': 'baru'}))
        self.assertEqual(CctvAkses.objects.get().password, 'baru')

    def test_delete_is_blocked_by_coverage(self):
        from django.contrib.gis.geos import MultiPolygon, Polygon
        cctv = self.make('LMO-BC-0001')
        CctvSpec.objects.create(cctv=cctv, brand='Sony')
        self.client.force_login(self.admin)

        CctvCoverage.objects.create(
            cctv=cctv, minggu='2026-10-26',
            geom=MultiPolygon(Polygon(((0, 0), (0, 1), (1, 1), (0, 0)))),
        )
        self.client.post(reverse('cctv:delete', args=[cctv.pk]))
        self.assertTrue(Cctv.objects.filter(pk=cctv.pk).exists())

        CctvCoverage.objects.all().delete()
        self.assertRedirects(self.client.post(reverse('cctv:delete', args=[cctv.pk])), reverse('cctv:index'))
        self.assertFalse(CctvSpec.objects.exists())
        self.assertEqual(Cctv.history.filter(id=cctv.pk).latest('history_date').history_type, '-')

    def test_detail_lokasi_options(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('cctv:detail_lokasi_options'), {'lokasi': self.lokasi.pk})
        self.assertEqual(response.json(), {'options': [{'id': self.detail.pk, 'nama': 'CR 01'}]})


class AccessScopeTests(TestCase):
    """Role per site + perusahaan: e.g. BMO 2 + PT Pamapersada only sees its own CCTV."""

    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.bmo2, cls.gmo = Site.objects.create(kode='BMO 2'), Site.objects.create(kode='GMO')
        cls.pama = Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')
        cls.buma = Perusahaan.objects.create(nama='PT Bukit Makmur Mandiri Utama', kode='BM')
        cls.own = Cctv.objects.create(no_cctv='BMO2-PM-0001', nama='A', site=cls.bmo2, perusahaan=cls.pama,
                                      posisi=Point(117.4, 2.0, srid=4326))
        cls.other_company = Cctv.objects.create(no_cctv='BMO2-BM-0001', nama='B', site=cls.bmo2,
                                                perusahaan=cls.buma, posisi=Point(117.5, 2.0, srid=4326))
        cls.other_site = Cctv.objects.create(no_cctv='GMO-PM-0001', nama='C', site=cls.gmo, perusahaan=cls.pama)

        cls.role = Group.objects.create(name='CCTV BMO 2 - PT Pamapersada Nusantara')
        CakupanCctv.objects.create(group=cls.role, site=cls.bmo2, perusahaan=cls.pama)
        cls.user = User.objects.create_user('pama', password='x')
        cls.user.groups.add(cls.role)
        cls.admin = User.objects.create_user('admin', password='x')
        cls.admin.groups.add(Group.objects.get(name=roles.ADMIN_HAZARD_MOTION))

    def setUp(self):
        self.client.force_login(self.user)

    def allow_edit(self):
        CakupanCctv.objects.filter(group=self.role).update(bisa_ubah=True)

    def form_data(self, **extra):
        data = {'no_cctv': self.own.no_cctv, 'nama': 'Baru', 'site': self.bmo2.pk, 'perusahaan': self.pama.pk,
                'status': 'live', 'kondisi': 'breakdown'}
        data.update(extra)
        return data

    def test_list_map_and_export_only_show_own_cctv(self):
        response = self.client.get(reverse('cctv:index'))
        self.assertEqual(response.context['stats']['total'], 1)
        self.assertContains(response, 'BMO2-PM-0001')
        self.assertNotContains(response, 'BMO2-BM-0001')
        self.assertNotContains(response, 'GMO-PM-0001')
        self.assertEqual([s.kode for s in response.context['sites']], ['BMO 2'])
        self.assertEqual([p.nama for p in response.context['perusahaan_list']], ['PT Pamapersada Nusantara'])

        # Filtering by another site cannot widen the scope.
        response = self.client.get(reverse('cctv:index'), {'site': self.gmo.pk})
        self.assertEqual(response.context['stats']['total'], 0)

        features = self.client.get(reverse('cctv:geojson')).json()['features']
        self.assertEqual([f['properties']['no'] for f in features], ['BMO2-PM-0001'])
        body = b''.join(self.client.get(reverse('cctv:export')).streaming_content).decode('utf-8-sig')
        self.assertIn('BMO2-PM-0001', body)
        self.assertNotIn('BMO2-BM-0001', body)

    def test_out_of_scope_cctv_is_not_found(self):
        self.assertEqual(self.client.get(reverse('cctv:detail', args=[self.own.pk])).status_code, 200)
        for cctv in (self.other_company, self.other_site):
            self.assertEqual(self.client.get(reverse('cctv:detail', args=[cctv.pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse('cctv:update', args=[cctv.pk])).status_code, 404)

    def test_view_only_role_cannot_change_anything(self):
        detail = self.client.get(reverse('cctv:detail', args=[self.own.pk]))
        self.assertNotContains(detail, reverse('cctv:update', args=[self.own.pk]))
        self.assertNotContains(self.client.get(reverse('cctv:index')), reverse('cctv:create'))
        self.assertEqual(self.client.get(reverse('cctv:create')).status_code, 403)
        self.assertEqual(self.client.post(reverse('cctv:update', args=[self.own.pk]), self.form_data()).status_code, 403)
        self.assertEqual(self.client.post(reverse('cctv:delete', args=[self.own.pk])).status_code, 403)
        self.own.refresh_from_db()
        self.assertEqual(self.own.kondisi, 'baik')

    def test_edit_role_changes_own_cctv_but_cannot_move_it_out(self):
        self.allow_edit()
        response = self.client.post(reverse('cctv:update', args=[self.own.pk]), self.form_data())
        self.assertRedirects(response, reverse('cctv:detail', args=[self.own.pk]))
        self.own.refresh_from_db()
        self.assertEqual(self.own.kondisi, 'breakdown')

        response = self.client.post(reverse('cctv:update', args=[self.own.pk]),
                                    self.form_data(perusahaan=self.buma.pk))
        self.assertEqual(response.status_code, 200)
        self.own.refresh_from_db()
        self.assertEqual(self.own.perusahaan, self.pama)

        response = self.client.post(reverse('cctv:create'), self.form_data(no_cctv='BMO2-PM-0002'))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse('cctv:create'), self.form_data(no_cctv='GMO-PM-0002', site=self.gmo.pk))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Cctv.objects.filter(no_cctv='GMO-PM-0002').exists())
        # Delete stays admin-only.
        self.assertEqual(self.client.post(reverse('cctv:delete', args=[self.own.pk])).status_code, 403)

    def test_site_only_scope_covers_every_company_there(self):
        CakupanCctv.objects.filter(group=self.role).update(perusahaan=None)
        visible = get_scope(get_user_model().objects.get(pk=self.user.pk)).filter(Cctv.objects.all())
        self.assertEqual(set(visible.values_list('no_cctv', flat=True)), {'BMO2-PM-0001', 'BMO2-BM-0001'})

    def test_admin_sees_and_manages_everything(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('cctv:index')).context['stats']['total'], 3)
        self.assertEqual(self.client.get(reverse('cctv:detail', args=[self.other_site.pk])).status_code, 200)
        self.assertTrue(get_scope(self.admin).can_delete)

    def test_password_endpoint_respects_scope(self):
        it_bmo2 = get_user_model().objects.create_user('it-bmo2', password='x')
        it_bmo2.groups.add(self.role, Group.objects.get(name=roles.IT_PAMA))
        CakupanCctv.objects.filter(group__name=roles.IT_PAMA).delete()  # narrow IT to this role's scope
        CctvAkses.objects.create(cctv=self.other_company, password='rahasia')
        self.client.force_login(it_bmo2)
        self.assertEqual(self.client.post(reverse('cctv:password', args=[self.other_company.pk])).status_code, 404)

    def test_menu_follows_scope(self):
        def titles(user):
            return [c['title'] for s in build_menu(user) for i in s['items'] for c in i['children']]

        self.assertIn('CCTV Database', titles(get_user_model().objects.get(pk=self.user.pk)))
        nobody = get_user_model().objects.create_user('nobody', password='x')
        self.assertNotIn('CCTV Database', titles(nobody))
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse('cctv:index')).status_code, 403)

    def test_it_roles_keep_full_scope_after_migration(self):
        rule = CakupanCctv.objects.get(group__name=roles.IT_PAMA)
        self.assertEqual((rule.site, rule.perusahaan, rule.bisa_ubah), (None, None, True))

    def test_buat_role_cctv_creates_one_role_per_pair(self):
        from django.core.management import call_command
        out = io.StringIO()
        call_command('buat_role_cctv', stdout=out)
        names = set(Group.objects.filter(name__startswith='CCTV ').values_list('name', flat=True))
        self.assertIn('CCTV GMO - PT Pamapersada Nusantara', names)
        self.assertIn('CCTV BMO 2 - PT Bukit Makmur Mandiri Utama', names)
        self.assertIn('2 role baru, 1 sudah ada', out.getvalue())  # BMO 2 - PAMA existed already
        call_command('buat_role_cctv', stdout=out)
        self.assertIn('0 role baru, 3 sudah ada', out.getvalue())
