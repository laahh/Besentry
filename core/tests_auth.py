"""BEATS login + SID sync. The APIs are mocked with the shape of their real responses."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import authenticate, get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import karyawan_sync, sid_api
from .models import Karyawan, Perusahaan

BEATS_OK = {
    'id': '1', 'username': 'S81UT', 'namaKaryawan': 'BUDI SANTOSO', 'email': 'BUDI.SANTOSO@BERAUCOAL.CO.ID',
    'idKaryawan': '80268', 'idPerusahaan': '5194', 'kodePerusahaan': '5HDCZ', 'namaPerusahaan': 'PT Berau Coal',
    'success': True, 'message': None,
}
DETAIL = {
    'employee': {
        'id': 80268, 'sidCode': 'S81UT', 'employeeIdNumber': '11001571', 'name': 'BUDI SANTOSO',
        'email': 'BUDI.SANTOSO@BERAUCOAL.CO.ID', 'company': {'id': 5194, 'name': 'PT Berau Coal'},
        'structuralPosition': {'name': 'DATA & MINING TECHNOLOGY SPECIALIST'},
        'functionalPosition': {'name': 'Engineer/Specialist'},
        'department': {'name': 'MINING TECHNOLOGY DEPARTMENT'},
        'status': {'name': 'AKTIF'}, 'statusPermit': 'PASSED',
    },
    'dedicatedSite': {'name': 'HO'},
}

login = mock.patch.object(sid_api, 'beats_login')
detail = mock.patch.object(sid_api, 'employee_detail', return_value=DETAIL)


@override_settings(SID_API_KEY='test-key', BEATS_OFFLINE_FALLBACK=True)
class BeatsLoginTests(TestCase):
    def sign_in(self, username='S81UT', password='rahasia'):
        return authenticate(username=username, password=password)

    @detail
    @login
    def test_first_login_creates_account_and_syncs_profile(self, beats_login, employee_detail):
        beats_login.return_value = BEATS_OK
        bc = Perusahaan.objects.create(nama='PT BERAU COAL', kode='BC')  # from the CCTV import

        user = self.sign_in()

        self.assertEqual((user.username, user.first_name, user.email),
                         ('S81UT', 'BUDI SANTOSO', 'budi.santoso@beraucoal.co.id'))
        self.assertTrue(user.check_password('rahasia'))
        karyawan = user.karyawan
        self.assertEqual(karyawan.perusahaan, bc)  # matched by name, then linked by companyId
        bc.refresh_from_db()
        self.assertEqual(bc.sid_id, 5194)
        self.assertEqual((karyawan.jabatan_struktural, karyawan.site, karyawan.status, karyawan.npk),
                         ('DATA & MINING TECHNOLOGY SPECIALIST', 'HO', 'AKTIF', '11001571'))

    @detail
    @login
    def test_detail_is_refetched_only_when_stale(self, beats_login, employee_detail):
        beats_login.return_value = BEATS_OK
        self.sign_in()
        self.sign_in()
        self.assertEqual(employee_detail.call_count, 1)

        Karyawan.objects.update(disinkron=timezone.now() - timedelta(hours=25))
        self.sign_in()
        self.assertEqual(employee_detail.call_count, 2)

    @login
    def test_wrong_password_does_not_fall_back_to_the_old_local_password(self, beats_login):
        beats_login.return_value = BEATS_OK
        with detail:
            self.sign_in(password='lama')
        beats_login.return_value = None  # password changed in BEATS
        self.assertIsNone(self.sign_in(password='lama'))

    @login
    def test_api_down_uses_the_last_good_password(self, beats_login):
        beats_login.return_value = BEATS_OK
        with detail:
            self.sign_in(password='terakhir')
        beats_login.side_effect = sid_api.ApiUnavailable('timeout')
        self.assertIsNotNone(self.sign_in(password='terakhir'))
        self.assertIsNone(self.sign_in(password='tebakan'))
        with override_settings(BEATS_OFFLINE_FALLBACK=False):
            self.assertIsNone(self.sign_in(password='terakhir'))

    @login
    def test_local_account_password_never_goes_to_beats(self, beats_login):
        get_user_model().objects.create_user('admin-lokal', password='lokal')
        self.assertIsNotNone(self.sign_in('admin-lokal', 'lokal'))
        beats_login.assert_not_called()

    @login
    def test_unknown_username_rejected_by_beats(self, beats_login):
        beats_login.return_value = None
        self.assertIsNone(self.sign_in('TIDAKADA', 'x'))
        self.assertFalse(get_user_model().objects.filter(username='TIDAKADA').exists())

    @login
    def test_nonaktif_employee_cannot_sign_in(self, beats_login):
        beats_login.return_value = BEATS_OK
        nonaktif = {**DETAIL, 'employee': {**DETAIL['employee'], 'status': {'name': 'NONAKTIF'}}}
        with mock.patch.object(sid_api, 'employee_detail', return_value=nonaktif):
            self.assertIsNone(self.sign_in())
        self.assertFalse(get_user_model().objects.get(username='S81UT').is_active)

    @detail
    @login
    def test_detail_api_down_still_signs_in(self, beats_login, employee_detail):
        beats_login.return_value = BEATS_OK
        employee_detail.side_effect = sid_api.ApiUnavailable('HTTP 502')
        user = self.sign_in()
        self.assertIsNotNone(user)
        self.assertIsNone(user.karyawan.disinkron)  # retried at the next login

    @detail
    @login
    def test_login_page(self, beats_login, employee_detail):
        beats_login.return_value = BEATS_OK
        response = self.client.post(reverse('login'), {'username': 'S81UT', 'password': 'rahasia'})
        self.assertRedirects(response, reverse('dashboard:index'), fetch_redirect_response=False)
        beats_login.return_value = None
        self.client.logout()
        response = self.client.post(reverse('login'), {'username': 'S81UT', 'password': 'salah'})
        self.assertContains(response, 'password salah')


class BeatsResponseTests(TestCase):
    def test_success_false_means_wrong_credentials(self):
        rejected = {'username': None, 'success': False, 'message': 'User is not recognized'}
        with mock.patch.object(sid_api, '_request', return_value=rejected):
            self.assertIsNone(sid_api.beats_login('X', 'y'))

    def test_unexpected_body_means_api_unavailable(self):
        with mock.patch.object(sid_api, '_request', return_value=['html?']):
            with self.assertRaises(sid_api.ApiUnavailable):
                sid_api.beats_login('X', 'y')


class EmployeeSyncTests(TestCase):
    ROWS = [
        {'sidCode': 'AAA01', 'employeeId': 1, 'npk': '1', 'name': 'ANI', 'email': 'ANI@X.ID', 'companyId': 5194,
         'departmentName': 'HSE', 'structuralPosition': 'Officer', 'functionalPosition': 'Staff',
         'dedicatedSite': 'LMO', 'status': 'AKTIF', 'statusPermit': 'PASSED', 'ktpNo': '3201', 'rfidCardNo': '9'},
        {'sidCode': 'BBB02', 'employeeId': 2, 'npk': '2', 'name': 'BUDI', 'email': '', 'companyId': 5194,
         'departmentName': '', 'structuralPosition': '', 'functionalPosition': '', 'dedicatedSite': '',
         'status': 'NONAKTIF', 'statusPermit': 'NOT PASSED'},
    ]

    def test_bulk_upsert_and_deactivate_nonaktif_logins(self):
        perusahaan = Perusahaan.objects.create(nama='PT Berau Coal', sid_id=5194)
        budi = get_user_model().objects.create_user('BBB02', password='x')
        Karyawan.objects.create(sid='BBB02', nama='BUDI', user=budi, status='AKTIF')

        with mock.patch.object(sid_api, 'employees', return_value=iter(self.ROWS)):
            report = karyawan_sync.sync_employees(perusahaan)

        self.assertEqual(report.karyawan, 2)
        self.assertEqual(report.dinonaktifkan, ['BBB02'])
        budi.refresh_from_db()
        self.assertFalse(budi.is_active)
        ani = Karyawan.objects.get(sid='AAA01')
        self.assertEqual((ani.site, ani.email, ani.perusahaan), ('LMO', 'ani@x.id', perusahaan))
        self.assertFalse(hasattr(ani, 'ktp'))  # personal data is not stored

        # Running it again updates in place.
        with mock.patch.object(sid_api, 'employees', return_value=iter(self.ROWS)):
            karyawan_sync.sync_employees(perusahaan)
        self.assertEqual(Karyawan.objects.count(), 2)

    def test_companies_link_by_name_and_keep_duplicates_apart(self):
        Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')
        Perusahaan.objects.create(nama='PT Sama', sid_id=1)
        companies = [{'companyId': 10, 'name': 'PT Pamapersada Nusantara'}, {'companyId': 11, 'name': 'PT Sama'}]
        with mock.patch.object(sid_api, 'companies', return_value=iter(companies)):
            karyawan_sync.sync_companies()
        self.assertEqual(Perusahaan.objects.get(kode='PM').sid_id, 10)
        self.assertEqual(Perusahaan.objects.get(sid_id=11).nama, 'PT Sama (11)')
