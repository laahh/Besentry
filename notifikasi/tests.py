import datetime
import io
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from cctv.models import CakupanCctv, Cctv, PengajuanCoverage, RiwayatPengajuan
from core import karyawan_sync
from core.models import Karyawan, Perusahaan, Site
from navigation import roles

from . import channels, services
from .models import Notifikasi, PicSgi, normalise_wa


def fonnte_reply(body):
    """A stand-in for urlopen(): a context manager yielding this JSON (fresh for each call)."""
    return mock.MagicMock(__enter__=lambda self: io.BytesIO(json.dumps(body).encode()),
                          __exit__=lambda *args: None)


FONNTE_OK = {'status': True, 'detail': 'success! message in queue'}


class PicTests(TestCase):
    def test_normalises_numbers(self):
        for raw in ('0812-3456-7890', '+62 812 3456 7890', '6281234567890'):
            self.assertEqual(normalise_wa(raw), '6281234567890')

    def test_needs_wa_or_email(self):
        with self.assertRaises(ValidationError):
            PicSgi(nama='Kosong').full_clean()
        pic = PicSgi(nama='Ada', no_wa='0812 3456 7890')
        pic.full_clean()
        self.assertEqual(pic.no_wa, '6281234567890')


@override_settings(FONNTE_TOKEN='test-token', SITE_URL='https://besentry.example')
class NotifyTests(TestCase):
    """Every step of a change request tells whoever works on the next one."""

    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')
        cls.cctv = Cctv.objects.create(no_cctv='LMO-PM-0099', nama='Pit J', site=cls.lmo, perusahaan=cls.perusahaan,
                                       posisi=Point(117.6, 2.2, srid=4326))
        cls.it = User.objects.create_user('it', password='x', email='it@example.com', first_name='Ifa')
        cls.it.groups.add(Group.objects.get(name=roles.IT_PAMA))

        lmo_role = Group.objects.create(name='CCTV LMO - PT Pamapersada Nusantara')
        CakupanCctv.objects.create(group=lmo_role, site=cls.lmo, perusahaan=cls.perusahaan)
        smo_role = Group.objects.create(name='CCTV SMO - PT Pamapersada Nusantara')
        CakupanCctv.objects.create(group=smo_role, site=cls.smo, perusahaan=cls.perusahaan)
        cls.mitra_lmo = cls._user('mitra-lmo', roles.SURVEY_MITRA, lmo_role, no_hp='0812 1111 1111')
        cls.mitra_smo = cls._user('mitra-smo', roles.SURVEY_MITRA, smo_role, no_hp='0812 2222 2222')
        cls.survey_bc = cls._user('survey-bc', roles.SURVEY_BC, email='bc@example.com')
        cls.safety = cls._user('safety', roles.SAFETY, no_hp='0812 3333 3333')
        cls.pic_lmo = PicSgi.objects.create(nama='SGI LMO', no_wa='6284444444444', site=cls.lmo)
        PicSgi.objects.create(nama='SGI SMO', no_wa='6285555555555', site=cls.smo)

    @classmethod
    def _user(cls, username, role, *groups, email='', no_hp=''):
        user = get_user_model().objects.create_user(username, password='x', email=email)
        user.groups.add(Group.objects.get(name=role), *groups)
        if no_hp:
            Karyawan.objects.create(sid=username, nama=username, no_hp=no_hp, user=user)
        return user

    def setUp(self):
        patcher = mock.patch.object(channels.urllib.request, 'urlopen', return_value=fonnte_reply(FONNTE_OK))
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, status, **extra):
        return PengajuanCoverage.objects.create(cctv=self.cctv, jenis='dipindah', status=status,
                                                minggu=datetime.date(2026, 11, 2), diajukan_oleh=self.it, **extra)

    def notify(self, pengajuan):
        with self.captureOnCommitCallbacks(execute=True):
            return services.notify_pengajuan(pengajuan, self.it)

    def test_waiting_survey_mitra_tells_mitra_in_scope_only(self):
        pengajuan = self.request('menunggu_mitra',
                                 perubahan=[['Koordinat', '2.200000, 117.600000', '2.200000, 117.601200']])
        self.notify(pengajuan)
        sent = Notifikasi.objects.get()
        self.assertEqual((sent.kanal, sent.tujuan, sent.status, sent.jenis),
                         ('wa', '6281211111111', 'terkirim', 'pengajuan'))
        self.assertIn('*Waiting Survey Mitra*', sent.pesan)
        self.assertIn('Koordinat: 2.200000, 117.600000 → 2.200000, 117.601200', sent.pesan)
        self.assertIn(f'https://besentry.example/cctv/pengajuan/{pengajuan.pk}/', sent.pesan)
        self.assertEqual(self.urlopen.call_args[0][0].headers['Authorization'], 'test-token')

    def test_survey_bc_gets_users_and_the_site_pic(self):
        self.notify(self.request('menunggu_sgi'))
        self.assertEqual(set(Notifikasi.objects.values_list('kanal', 'tujuan')),
                         {('email', 'bc@example.com'), ('wa', '6284444444444')})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] Waiting Survey BC: LMO-PM-0099')
        self.assertNotIn('*', mail.outbox[0].body)  # WhatsApp bold markers are stripped for email

    def test_safety_and_return_to_it(self):
        self.notify(self.request('menunggu_safety'))
        self.assertEqual(list(Notifikasi.objects.values_list('tujuan', flat=True)), ['6281233333333'])
        Notifikasi.objects.all().delete()

        PengajuanCoverage.objects.all().delete()
        pengajuan = self.request('return_it')
        RiwayatPengajuan.objects.create(pengajuan=pengajuan, aksi='dikembalikan', catatan='Safety BC ke IT: geser')
        self.notify(pengajuan)
        sent = Notifikasi.objects.get()
        self.assertEqual(sent.tujuan, 'it@example.com')
        self.assertIn('Catatan: Safety BC ke IT: geser', sent.pesan)

    def test_approved_tells_nobody(self):
        self.assertEqual(self.notify(self.request('approved')), [])

    def test_failed_delivery_is_logged_and_can_be_resent(self):
        self.urlopen.return_value = fonnte_reply({'status': False, 'reason': 'invalid token'})
        self.notify(self.request('menunggu_safety'))
        failed = Notifikasi.objects.get()
        self.assertEqual(failed.status, 'gagal')
        self.assertIn('invalid token', failed.error)

        self.urlopen.return_value = fonnte_reply(FONNTE_OK)
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get(name=roles.ADMIN_HAZARD_MOTION))
        self.client.force_login(admin)
        self.client.post(reverse('notifikasi:resend', args=[failed.pk]))
        failed.refresh_from_db()
        self.assertEqual((failed.status, failed.percobaan), ('terkirim', 2))

    def test_unreachable_fonnte_does_not_break_the_flow(self):
        self.urlopen.side_effect = channels.urllib.error.URLError('no route')
        self.notify(self.request('menunggu_sgi'))
        self.assertEqual(Notifikasi.objects.get(kanal='wa').status, 'gagal')
        self.assertEqual(Notifikasi.objects.get(kanal='email').status, 'terkirim')


DETAIL = {
    'employee': {'id': 1, 'sidCode': 'S81UT', 'name': 'BUDI SANTOSO', 'email': 'BUDI@BERAUCOAL.CO.ID',
                 'phone': '0812-3456-7890', 'company': {'id': 5194, 'name': 'PT Berau Coal'},
                 'structuralPosition': {'name': 'GIS SPECIALIST'}, 'status': {'name': 'AKTIF'}},
    'dedicatedSite': {'name': 'HO'},
}


@override_settings(FONNTE_TOKEN='test-token', SID_API_KEY='')
class PicPageTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('admin', password='x')
        self.admin.groups.add(Group.objects.get(name=roles.ADMIN_HAZARD_MOTION))
        self.client.force_login(self.admin)
        self.karyawan = Karyawan.objects.create(sid='S81UT', nama='BUDI SANTOSO', email='budi@beraucoal.co.id',
                                                no_hp='081234567890', jabatan_struktural='GIS SPECIALIST',
                                                status='AKTIF')
        Karyawan.objects.create(sid='OLD01', nama='BUDI LAMA', status='NONAKTIF')

    def test_admin_only(self):
        self.client.force_login(get_user_model().objects.create_user('biasa', password='x'))
        self.assertEqual(self.client.get(reverse('notifikasi:pic_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('notifikasi:cari_karyawan'), {'q': 'budi'}).status_code, 403)

    def test_search_synced_employees_active_first(self):
        results = self.client.get(reverse('notifikasi:cari_karyawan'), {'q': 'budi'}).json()['results']
        self.assertEqual([r['sid'] for r in results], ['S81UT', 'OLD01'])
        self.assertEqual(results[0]['no_hp'], '081234567890')
        self.assertEqual(self.client.get(reverse('notifikasi:cari_karyawan'), {'q': 'b'}).json(), {'results': []})

    def test_unsynced_sid_is_looked_up_in_the_sid_api(self):
        with override_settings(SID_API_KEY='key'), \
                mock.patch.object(karyawan_sync.sid_api, 'employee_detail', return_value={
                    **DETAIL, 'employee': {**DETAIL['employee'], 'sidCode': 'NEW99', 'name': 'ANI BARU'}}):
            results = self.client.get(reverse('notifikasi:cari_karyawan'), {'q': 'NEW99'}).json()['results']
        self.assertEqual((results[0]['nama'], results[0]['no_hp']), ('ANI BARU', '0812-3456-7890'))
        self.assertTrue(Karyawan.objects.filter(sid='NEW99').exists())

    def test_pic_takes_name_email_and_phone_from_sid(self):
        with override_settings(SID_API_KEY='key'), \
                mock.patch.object(karyawan_sync.sid_api, 'employee_detail', return_value=DETAIL):
            self.client.post(reverse('notifikasi:pic_list'), {'sid': 's81ut', 'aktif': 'on'})
        pic = PicSgi.objects.get()
        self.assertEqual((pic.karyawan, pic.nama, pic.email, pic.no_wa),
                         (self.karyawan, 'BUDI SANTOSO', 'budi@beraucoal.co.id', '6281234567890'))

    def test_typed_number_overrides_sid_and_unknown_sid_is_refused(self):
        self.client.post(reverse('notifikasi:pic_list'), {'sid': 'S81UT', 'no_wa': '0899 1111 2222', 'aktif': 'on'})
        self.assertEqual(PicSgi.objects.get().no_wa, '6289911112222')
        response = self.client.post(reverse('notifikasi:pic_list'), {'sid': 'ZZZ99'})
        self.assertContains(response, 'SID tidak ditemukan')

    def test_add_edit_test_and_delete(self):
        self.client.post(reverse('notifikasi:pic_list'), {'sid': 'S81UT', 'aktif': 'on'})
        pic = PicSgi.objects.get()
        self.assertEqual(pic.no_wa, '6281234567890')

        self.client.post(reverse('notifikasi:pic_update', args=[pic.pk]),
                         {'sid': 'S81UT', 'no_wa': '', 'email': 'b@example.com'})
        pic.refresh_from_db()
        # A cleared WhatsApp falls back to the SID number; aktif unticked.
        self.assertEqual((pic.nama, pic.email, pic.no_wa, pic.aktif),
                         ('BUDI SANTOSO', 'b@example.com', '6281234567890', False))

        with mock.patch.object(channels.urllib.request, 'urlopen', return_value=fonnte_reply(FONNTE_OK)):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse('notifikasi:pic_test', args=[pic.pk]))
        self.assertEqual(set(Notifikasi.objects.values_list('status', flat=True)), {'terkirim'})
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] Tes notifikasi')

        self.client.post(reverse('notifikasi:pic_delete', args=[pic.pk]))
        self.assertFalse(PicSgi.objects.exists())
        self.assertFalse(Notifikasi.objects.filter(pic__isnull=False).exists())  # the log stays, unlinked
        self.assertTrue(Notifikasi.objects.exists())

    def test_send_wa_needs_a_token(self):
        with override_settings(FONNTE_TOKEN=''):
            with self.assertRaisesMessage(channels.DeliveryError, 'FONNTE_TOKEN'):
                channels.send_wa('6281234567890', 'x')
