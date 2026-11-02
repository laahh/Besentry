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

from cctv.models import Cctv
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
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal', kode='BC')
        cls.admin = get_user_model().objects.create_user('admin', password='x', first_name='Ifa')
        cls.admin.groups.add(Group.objects.get(name=roles.ADMIN_HAZARD_MOTION))
        cls.pic_all = PicSgi.objects.create(nama='SGI Pusat', no_wa='6281111111111', email='sgi@example.com')
        cls.pic_lmo = PicSgi.objects.create(nama='SGI LMO', no_wa='6282222222222', site=cls.lmo)
        cls.pic_smo = PicSgi.objects.create(nama='SGI SMO', email='smo@example.com', site=cls.smo)
        PicSgi.objects.create(nama='Nonaktif', no_wa='6283333333333', aktif=False)

    def setUp(self):
        self.client.force_login(self.admin)
        patcher = mock.patch.object(channels.urllib.request, 'urlopen', return_value=fonnte_reply(FONNTE_OK))
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)

    def form_data(self, **extra):
        data = {'no_cctv': 'LMO-BC-0099', 'nama': 'Pit J', 'site': self.lmo.pk, 'perusahaan': self.perusahaan.pk,
                'status': 'live', 'kondisi': 'baik', 'longitude': '117.6', 'latitude': '2.2',
                'lokasi_pemasangan': 'Bench 3'}
        data.update(extra)
        return data

    def test_new_cctv_notifies_pics_of_its_site_by_wa_and_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('cctv:create'), self.form_data(), follow=True)
        # TestCase never commits, so sending runs when the block ends (after the page was built).
        self.assertContains(response, 'Notifikasi ke 2 PIC SGI')

        sent = Notifikasi.objects.order_by('tujuan')
        self.assertEqual([(n.kanal, n.tujuan) for n in sent],
                         [('wa', '6281111111111'), ('wa', '6282222222222'), ('email', 'sgi@example.com')])
        self.assertTrue(all(n.status == 'terkirim' and n.jenis == 'cctv_baru' for n in sent))
        self.assertEqual(self.urlopen.call_count, 2)
        request = self.urlopen.call_args[0][0]
        self.assertEqual(request.headers['Authorization'], 'test-token')
        self.assertIn(b'LMO-BC-0099', request.data)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] CCTV baru: LMO-BC-0099')
        body = mail.outbox[0].body
        self.assertIn('https://besentry.example/spasial/digitasi/?id=', body)  # the SGI task
        self.assertIn('maps.google.com/?q=2.200000,117.600000', body)
        self.assertNotIn('*', body)  # WhatsApp bold markers are stripped for email

    def test_only_a_location_change_notifies(self):
        cctv = Cctv.objects.create(no_cctv='LMO-BC-0099', nama='Pit J', site=self.lmo, perusahaan=self.perusahaan,
                                   posisi=Point(117.6, 2.2, srid=4326), lokasi_pemasangan='Bench 3')
        url = reverse('cctv:update', args=[cctv.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.form_data(nama='Pit J baru', kondisi='breakdown'))
            self.client.post(url, self.form_data(longitude='117.600005'))  # ~0.5 m: GPS jitter
        self.assertFalse(Notifikasi.objects.exists())

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.form_data(longitude='117.6012', lokasi_pemasangan='Bench 5'))
        notifikasi = Notifikasi.objects.filter(kanal='wa').first()
        self.assertEqual(notifikasi.jenis, 'reposisi')
        self.assertIn('Lokasi pemasangan: Bench 3 → Bench 5', notifikasi.pesan)
        # The 0.5 m edit above was saved (just not notified), so it is the "before" here.
        self.assertIn('Koordinat: 2.200000, 117.600005 → 2.200000, 117.601200', notifikasi.pesan)

    def test_moving_to_another_site_tells_that_sites_pic(self):
        cctv = Cctv.objects.create(no_cctv='LMO-BC-0099', nama='Pit J', site=self.lmo, perusahaan=self.perusahaan,
                                   posisi=Point(117.6, 2.2, srid=4326), lokasi_pemasangan='Bench 3')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cctv:update', args=[cctv.pk]), self.form_data(site=self.smo.pk))
        self.assertEqual(set(Notifikasi.objects.values_list('pic__nama', flat=True)), {'SGI Pusat', 'SGI SMO'})
        self.assertIn('Site: LMO → SMO', Notifikasi.objects.first().pesan)

    def test_failed_delivery_is_logged_and_can_be_resent(self):
        self.urlopen.return_value = fonnte_reply({'status': False, 'reason': 'invalid token'})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cctv:create'), self.form_data())
        failed = Notifikasi.objects.filter(kanal='wa').first()
        self.assertEqual(failed.status, 'gagal')
        self.assertIn('invalid token', failed.error)

        self.urlopen.return_value = fonnte_reply(FONNTE_OK)
        self.client.post(reverse('notifikasi:resend', args=[failed.pk]))
        failed.refresh_from_db()
        self.assertEqual((failed.status, failed.percobaan), ('terkirim', 2))

    def test_unreachable_fonnte_does_not_break_saving(self):
        self.urlopen.side_effect = channels.urllib.error.URLError('no route')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cctv:create'), self.form_data())
        self.assertTrue(Cctv.objects.filter(no_cctv='LMO-BC-0099').exists())
        self.assertEqual(list(Notifikasi.objects.filter(kanal='wa').values_list('status', flat=True)),
                         ['gagal', 'gagal'])
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
