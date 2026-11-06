import zipfile
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from core.models import DetailLokasi, Lokasi, Perusahaan, Site
from navigation import roles
from notifikasi import channels
from notifikasi.models import Notifikasi, Pic

from . import excel_cctv
from .models import CakupanCctv, Cctv, ControlRoom, CctvSpec, PengajuanCoverage

URL = '/cctv/upload-excel/'
TEMPLATE_URL = '/cctv/upload-excel/template.xlsx'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20


def screenshots(*numbers, extra=None):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for number in numbers:
            archive.writestr(f'{number}.png', PNG)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return SimpleUploadedFile('gambar.zip', buffer.getvalue(), content_type='application/zip')


@override_settings(FONNTE_TOKEN='test-token', SITE_URL='https://besentry.example')
class UploadExcelBase(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.fad = Perusahaan.objects.create(nama='PT Fajar', kode='FAD')
        cls.bc = Perusahaan.objects.create(nama='PT Berau Coal', kode=Perusahaan.KODE_BERAU_COAL)
        cls.room = ControlRoom.objects.create(site=cls.lmo, nama='CR Utama')
        cls.lokasi = Lokasi.objects.create(nama='Pit J', site=cls.lmo)
        cls.detail = DetailLokasi.objects.create(lokasi=cls.lokasi, nama='Ramp 1')
        cls.admin = get_user_model().objects.create_user('admin', password='x')
        for role in (roles.ADMIN, roles.IT):  # IT roles see connection details
            cls.admin.groups.add(Group.objects.get_or_create(name=role)[0])

    def setUp(self):
        self.client.force_login(self.admin)
        patcher = mock.patch.object(channels, 'send_wa')
        patcher.start()
        self.addCleanup(patcher.stop)

    def workbook(self, *rows):
        """A filled copy of the real template, so a change in the template shows up here."""
        book = load_workbook(BytesIO(self.client.get(TEMPLATE_URL).content))
        sheet = book[excel_cctv.SHEET_DATA]
        headers = {str(cell.value).replace('*', '').strip(): cell.column for cell in sheet[1]}
        for number, row in enumerate(rows, start=2):
            for header, value in row.items():
                sheet.cell(row=number, column=headers[header], value=value)
        output = BytesIO()
        book.save(output)
        return SimpleUploadedFile('cctv.xlsx', output.getvalue())

    def row(self, number='LMO-FAD-1', **extra):
        return {'No. CCTV': number, 'Nama CCTV': 'Kamera', 'Site': 'LMO', 'Perusahaan': 'PT Fajar', **extra}

    def post(self, upload, aksi, gambar=None):
        data = {'file_excel': upload, 'aksi': aksi}
        if gambar:
            data['file_gambar'] = gambar
        return self.client.post(URL, data)

    def by_number(self, response):
        return {row['no_cctv']: row for row in response.context['rows']}


class TemplateTests(UploadExcelBase):
    def test_download_is_a_workbook_with_every_column_and_lists(self):
        response = self.client.get(TEMPLATE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn('template_upload_cctv.xlsx', response['Content-Disposition'])
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book.sheetnames, ['CCTV', 'Pilihan', 'Petunjuk'])
        self.assertEqual(book['Pilihan'].sheet_state, 'hidden')
        headers = [cell.value for cell in book['CCTV'][1]]
        self.assertEqual(headers[:4], ['No. CCTV *', 'Nama CCTV *', 'Site *', 'Perusahaan *'])
        self.assertIn('Group / nama NVR', headers)
        self.assertTrue(book['CCTV'].data_validations.dataValidation)
        options = [row[2].value for row in book['Pilihan'].iter_rows(min_row=2)]
        self.assertIn('LMO', options)

    def test_never_asks_for_a_password(self):
        book = load_workbook(BytesIO(self.client.get(TEMPLATE_URL).content))
        self.assertFalse([cell.value for cell in book['CCTV'][1] if 'password' in cell.value.casefold()])

    def test_lists_only_what_the_user_may_add_and_no_connection_details(self):
        user = get_user_model().objects.create_user('it', password='x')
        role = Group.objects.create(name='CCTV LMO FAD')
        CakupanCctv.objects.create(group=role, site=self.lmo, perusahaan=self.fad, bisa_ubah=True)
        user.groups.add(role)
        self.client.force_login(user)
        book = load_workbook(BytesIO(self.client.get(TEMPLATE_URL).content))
        options = list(book['Pilihan'].iter_rows(min_row=2, values_only=True))
        self.assertEqual({row[2] for row in options} - {None}, {'LMO'})
        self.assertEqual({row[3] for row in options} - {None}, {'PT Fajar'})
        self.assertNotIn('Group / nama NVR', [cell.value for cell in book['CCTV'][1]])

    def test_a_name_that_looks_like_a_formula_stays_text(self):
        Lokasi.objects.create(nama='=HYPERLINK("http://x")', site=self.lmo)
        book = load_workbook(BytesIO(self.client.get(TEMPLATE_URL).content))
        cells = [cell for row in book['Pilihan'].iter_rows() for cell in row if str(cell.value).startswith('=HYPER')]
        self.assertEqual([cell.data_type for cell in cells], ['s'])

    def test_someone_who_may_not_add_cctv_gets_nothing(self):
        user = get_user_model().objects.create_user('safety', password='x')
        user.groups.add(Group.objects.get_or_create(name=roles.SAFETY)[0])
        self.client.force_login(user)
        self.assertEqual(self.client.get(TEMPLATE_URL).status_code, 403)
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(URL).status_code, 302)

    def test_pages_offer_it(self):
        page = self.client.get(reverse('cctv:pengajuan_baru'))
        self.assertContains(page, URL)
        self.assertContains(page, TEMPLATE_URL)
        self.assertContains(self.client.get(reverse('cctv:index')), URL)
        self.assertContains(self.client.get(URL), TEMPLATE_URL)


class CheckTests(UploadExcelBase):
    def test_check_reports_each_row_without_saving(self):
        response = self.post(self.workbook(self.row('A-1'), self.row('A-2', Site='XXX')), 'cek')
        self.assertEqual(response.status_code, 200)
        rows = self.by_number(response)
        self.assertEqual(rows['A-1']['tujuan'], 'Draft IT')
        self.assertTrue(rows['A-1']['valid'])
        self.assertIn('Site: "XXX" tidak ditemukan', rows['A-2']['pesan'])
        self.assertEqual(response.context['jumlah_lolos'], 1)
        self.assertFalse(response.context['rows'][0]['valid'])
        self.assertContains(response, 'data-lolos="1"')
        self.assertFalse(Cctv.objects.exists())
        self.assertFalse(PengajuanCoverage.objects.exists())

    def test_row_problems_are_named(self):
        Cctv.objects.create(no_cctv='ADA-1', nama='K', site=self.lmo, perusahaan=self.fad)
        PengajuanCoverage.objects.create(jenis='baru', data_draft={'no_cctv': ['DRAFT-1']}, diajukan_oleh=self.admin)
        cases = {
            'ada-1': (self.row('ada-1'), 'sudah ada di CCTV Database'),
            'draft-1': (self.row('draft-1'), 'sudah ada di draft'),
            'B-1': (self.row('B-1', Kategori='Salah'), 'Kategori: "Salah" tidak ada di daftar'),
            'B-2': (self.row('B-2', Connected='mungkin'), 'harus Ya atau Tidak'),
            'B-3': (self.row('B-3', **{'Periode update': 'besok'}), 'tanggal "besok" tidak dikenal'),
            'B-4': (self.row('B-4', Longitude=117.5), 'longitude dan latitude'),
            'B-5': (self.row('B-5', Longitude=999, Latitude=2), 'Longitude'),
            'B-6': (self.row('B-6', Lokasi='Tidak ada'), 'Lokasi: "Tidak ada" tidak ditemukan'),
            'B-7': (self.row('B-7', **{'Control room': 'Tidak ada'}), 'Control room: "Tidak ada" tidak ditemukan'),
            'B-8': (self.row('B-8', Lokasi='Pit J', **{'Detail lokasi': 'Ramp 9'}), 'Detail lokasi: "Ramp 9"'),
        }
        rows = self.by_number(self.post(self.workbook(*[row for row, _ in cases.values()]), 'cek'))
        for number, (_, message) in cases.items():
            with self.subTest(number):
                self.assertFalse(rows[number]['valid'])
                self.assertIn(message, rows[number]['pesan'])

    def test_blank_number_and_required_fields_are_reported(self):
        rows = self.by_number(self.post(self.workbook({'Nama CCTV': 'K', 'Site': 'LMO'}), 'cek'))
        self.assertFalse(rows['(kosong)']['valid'])
        self.assertIn('No. CCTV', rows['(kosong)']['pesan'])
        self.assertIn('Perusahaan', rows['(kosong)']['pesan'])

    def test_same_number_twice_in_the_file_fails_both(self):
        response = self.post(self.workbook(self.row('C-1'), self.row('c-1')), 'cek')
        self.assertEqual(response.context['jumlah_lolos'], 0)
        self.assertIn('muncul 2 kali', response.context['rows'][0]['pesan'])

    def test_outside_the_users_scope_is_refused_per_row(self):
        user = get_user_model().objects.create_user('it', password='x')
        role = Group.objects.create(name='CCTV LMO FAD')
        CakupanCctv.objects.create(group=role, site=self.lmo, perusahaan=self.fad, bisa_ubah=True)
        user.groups.add(role)
        self.client.force_login(user)
        rows = self.by_number(self.post(self.workbook(self.row('D-1'), self.row('D-2', Site='SMO')), 'cek'))
        self.assertTrue(rows['D-1']['valid'])
        self.assertFalse(rows['D-2']['valid'])

    def test_whole_file_problems_are_reported(self):
        plain = Workbook()
        plain.active.append(['Bukan', 'Template'])
        plain.active.append(['x', 'y'])
        output = BytesIO()
        plain.save(output)
        too_many = [self.row(f'M-{index}') for index in range(excel_cctv.MAX_ROWS + 1)]
        cases = (
            (SimpleUploadedFile('x.xlsx', b'bukan excel'), 'harus berupa Excel'),
            (SimpleUploadedFile('y.xlsx', output.getvalue()), 'Gunakan template'),
            (self.workbook(), 'Tidak ada baris'),
            (self.workbook(*too_many), 'lebih dari'),
        )
        for upload, message in cases:
            with self.subTest(message):
                self.assertContains(self.post(upload, 'cek'), message, status_code=400)

    def test_oversized_excel_is_refused(self):
        with mock.patch('cctv.pengajuan_forms.EXCEL_MAX_MB', 0):
            response = self.post(self.workbook(self.row()), 'cek')
        self.assertContains(response, 'Maksimal', status_code=400)

    def test_screenshots_that_cannot_be_used_are_reported(self):
        gambar = screenshots(extra={'E-1.png': b'bukan gambar', 'lebih.png': PNG})
        response = self.post(self.workbook(self.row('E-1')), 'cek', gambar)
        row = response.context['rows'][0]
        self.assertFalse(row['valid'])
        self.assertIn('gambar', row['pesan'])
        self.assertEqual(response.context['gambar_lebih'], ['lebih.png'])

    def test_a_bad_screenshot_zip_is_a_whole_file_problem(self):
        response = self.post(self.workbook(self.row()), 'cek', SimpleUploadedFile('g.zip', b'bukan zip'))
        self.assertContains(response, 'harus berupa .zip', status_code=400)

    def test_screenshot_above_the_limit_is_refused_before_reading_it(self):
        with mock.patch.object(excel_cctv, 'SCREENSHOT_MAX_MB', 0):
            response = self.post(self.workbook(self.row('E-2')), 'cek', screenshots('E-2'))
        self.assertIn('melebihi', response.context['rows'][0]['pesan'])

    def test_names_are_escaped(self):
        response = self.post(self.workbook(self.row('<b>X</b>', Site='<i>y</i>')), 'cek')
        self.assertNotContains(response, '<b>X</b>')
        self.assertNotContains(response, '<i>y</i>')


class SaveTests(UploadExcelBase):
    def test_a_row_with_a_screenshot_continues_to_digitising(self):
        full = self.row(
            'F-1', Kategori='Mining Eyes', Status='Offline', Kondisi='Repair', Connected='Ya', Mirrored='Tidak',
            Longitude=117.5, Latitude=2.15, Keterangan='lewat excel', Brand='Hikvision',
            **{'Control room': 'CR Utama', 'Radius pengawasan (m)': 150, 'Periode update': '01/11/2026',
               'Lokasi': 'pit j', 'Detail lokasi': 'Ramp 1', 'IP CCTV': '10.0.0.1'},
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post(self.workbook(full), 'proses', screenshots('F-1'))
        self.assertRedirects(response, reverse('cctv:index'), fetch_redirect_response=False)
        cctv = Cctv.objects.get(no_cctv='F-1')
        self.assertEqual((cctv.site, cctv.perusahaan, cctv.control_room), (self.lmo, self.fad, self.room))
        self.assertEqual((cctv.lokasi, cctv.detail_lokasi), (self.lokasi, self.detail))
        self.assertEqual((cctv.kategori, cctv.status, cctv.kondisi), ('mining_eyes', 'offline', 'repair'))
        self.assertEqual((cctv.connected, cctv.mirrored), (True, False))
        self.assertEqual((cctv.posisi.x, cctv.posisi.y), (117.5, 2.15))
        self.assertEqual(cctv.periode_update.isoformat(), '2026-11-01')
        self.assertEqual(CctvSpec.objects.get(cctv=cctv).radius_m, 150)
        self.assertEqual(cctv.akses.ip_cctv, '10.0.0.1')
        pengajuan = PengajuanCoverage.objects.get(cctv=cctv)
        self.assertEqual((pengajuan.status, pengajuan.jenis), ('menunggu_mitra', 'baru'))
        self.assertEqual(pengajuan.diajukan_oleh, self.admin)
        self.assertTrue(pengajuan.tangkapan_layar)
        self.assertEqual(pengajuan.keterangan, 'lewat excel')

    def test_a_row_without_a_screenshot_becomes_a_draft(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post(self.workbook(self.row('G-1', Brand='Dahua', Connected='Ya')), 'proses')
        self.assertFalse(Cctv.objects.filter(no_cctv='G-1').exists())
        draft = PengajuanCoverage.objects.get()
        self.assertEqual((draft.status, draft.no_cctv, draft.diajukan_oleh), ('draft_it', 'G-1', self.admin))
        self.assertEqual(draft.data_draft['spec-brand'], ['Dahua'])
        self.assertEqual(draft.data_draft['connected'], ['on'])
        self.assertNotIn('aksi', draft.data_draft)
        self.assertEqual(Notifikasi.objects.count(), 0)

    def test_the_draft_opens_filled_in_and_can_continue(self):
        self.post(self.workbook(self.row('G-2', Brand='Dahua')), 'proses')
        draft = PengajuanCoverage.objects.get()
        page = self.client.get(reverse('cctv:pengajuan', args=[draft.pk]))
        self.assertContains(page, 'G-2')
        self.assertContains(page, 'Dahua')
        data = {key: values for key, values in draft.data_draft.items()}
        response = self.client.post(reverse('cctv:pengajuan_it', args=[draft.pk]), {
            **data, 'aksi': 'lanjutkan', 'tangkapan_layar': SimpleUploadedFile('a.png', PNG),
        })
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'menunggu_mitra')
        self.assertEqual(draft.cctv.no_cctv, 'G-2')

    def test_what_fails_stays_out_and_the_rest_is_saved(self):
        self.post(self.workbook(self.row('H-1'), self.row('H-2', Site='XXX'), self.row('H-3')), 'proses',
                  screenshots('H-1'))
        self.assertEqual(set(Cctv.objects.values_list('no_cctv', flat=True)), {'H-1'})
        self.assertEqual(PengajuanCoverage.objects.filter(status='draft_it').count(), 1)

    def test_uploading_the_same_file_again_repeats_nothing(self):
        self.post(self.workbook(self.row('I-1'), self.row('I-2')), 'proses', screenshots('I-1'))
        self.post(self.workbook(self.row('I-1'), self.row('I-2')), 'proses', screenshots('I-1'))
        self.assertEqual(Cctv.objects.count(), 1)
        self.assertEqual(PengajuanCoverage.objects.count(), 2)

    def test_a_row_that_fails_while_saving_does_not_stop_the_others(self):
        from . import upload_excel_views as views
        real = views._save_new_cctv

        def fail_once(forms, *args):
            if forms['form'].cleaned_data['no_cctv'] == 'J-1':
                raise views.IntegrityError('tabrakan')
            return real(forms, *args)

        with mock.patch.object(views, '_save_new_cctv', side_effect=fail_once):
            self.post(self.workbook(self.row('J-1'), self.row('J-2')), 'proses', screenshots('J-1', 'J-2'))
        self.assertEqual(set(Cctv.objects.values_list('no_cctv', flat=True)), {'J-2'})

    def test_survey_mitra_gets_one_message_for_the_whole_upload(self):
        Pic.objects.create(nama='Mitra', tahap='mitra', email='mitra@example.com')
        with self.captureOnCommitCallbacks(execute=True):
            self.post(self.workbook(self.row('K-1'), self.row('K-2')), 'proses', screenshots('K-1', 'K-2'))
        self.assertEqual(Notifikasi.objects.filter(tujuan='mitra@example.com').count(), 1)
        self.assertIn('2 CCTV', Notifikasi.objects.get(tujuan='mitra@example.com').judul)

    def test_berau_coal_goes_straight_to_survey_bc_and_is_told_to_digitise(self):
        Pic.objects.create(nama='Sgi', tahap='sgi', email='sgi@example.com')
        Pic.objects.create(nama='Mitra', tahap='mitra', email='mitra@example.com')
        row = self.row('L-1', Perusahaan='PT Berau Coal')
        with self.captureOnCommitCallbacks(execute=True):
            self.post(self.workbook(row, self.row('L-2')), 'proses', screenshots('L-1', 'L-2'))
        self.assertEqual(PengajuanCoverage.objects.get(cctv__no_cctv='L-1').status, 'menunggu_sgi')
        message = Notifikasi.objects.get(tujuan='sgi@example.com').pesan
        self.assertIn('Mohon digitasi dan verifikasi', message)
        self.assertEqual(Notifikasi.objects.filter(tujuan='mitra@example.com').count(), 1)

    def test_all_rows_share_one_transaction_so_live_coverage_is_recomputed_once(self):
        # Live coverage runs once per transaction, and TestCase is itself one, so counting runs here
        # proves nothing. Each row must sit in an outer atomic block of the view, around its savepoint.
        from . import upload_excel_views as views
        real, depths = views._save_row, []

        def spy(*args):
            depths.append(len(connection.savepoint_ids))
            return real(*args)

        upload = self.workbook(*[self.row(f'N-{i}') for i in range(3)])
        gambar = screenshots('N-0', 'N-1', 'N-2')
        baseline = len(connection.savepoint_ids)
        with mock.patch.object(views, '_save_row', side_effect=spy):
            self.post(upload, 'proses', gambar)
        self.assertEqual(Cctv.objects.count(), 3)
        self.assertEqual(depths, [baseline + 2] * 3)  # the file's block, and the row's savepoint
