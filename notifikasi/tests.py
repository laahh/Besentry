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

from cctv.access import get_scope
from cctv.models import CakupanCctv, Cctv, PengajuanCoverage, RiwayatPengajuan
from core import karyawan_sync
from core.models import Karyawan, Perusahaan, Site
from navigation import roles

from . import channels, services
from .forms import PicForm
from .models import Notifikasi, Pic, normalise_wa


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
            Pic(nama='Kosong', tahap='sgi').full_clean()
        pic = Pic(nama='Ada', tahap='sgi', no_wa='0812 3456 7890')
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
        cls.it.groups.add(Group.objects.get(name=roles.IT))

        lmo_role = Group.objects.create(name='CCTV LMO - PT Pamapersada Nusantara')
        CakupanCctv.objects.create(group=lmo_role, site=cls.lmo, perusahaan=cls.perusahaan)
        smo_role = Group.objects.create(name='CCTV SMO - PT Pamapersada Nusantara')
        CakupanCctv.objects.create(group=smo_role, site=cls.smo, perusahaan=cls.perusahaan)
        cls.mitra_lmo = cls._user('mitra-lmo', roles.SURVEY_MITRA, lmo_role, no_hp='0812 1111 1111')
        cls.mitra_smo = cls._user('mitra-smo', roles.SURVEY_MITRA, smo_role, no_hp='0812 2222 2222')
        cls.survey_bc = cls._user('survey-bc', roles.SURVEY_BC, email='bc@example.com')
        cls.safety = cls._user('safety', roles.SAFETY, no_hp='0812 3333 3333')

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

    def test_stage_without_pic_tells_the_users_of_its_role(self):
        self.notify(self.request('menunggu_sgi'))
        self.assertEqual(list(Notifikasi.objects.values_list('kanal', 'tujuan')), [('email', 'bc@example.com')])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] Waiting Survey BC: LMO-PM-0099')
        self.assertNotIn('*', mail.outbox[0].body)  # WhatsApp bold markers are stripped for email

    def test_assigned_pics_replace_the_role_users(self):
        Pic.objects.create(nama='SGI semua', tahap='sgi', no_wa='6284444444444')
        pic = Pic.objects.get()
        self.notify(self.request('menunggu_sgi'))
        sent = Notifikasi.objects.get()
        self.assertEqual((sent.tujuan, sent.pic), ('6284444444444', pic))

    def test_pic_must_cover_the_site_and_perusahaan_and_stage(self):
        other = Perusahaan.objects.create(nama='PT Lain')
        Pic.objects.create(nama='Site cocok', tahap='safety', no_wa='6281000000001', site=self.lmo)
        Pic.objects.create(nama='Perusahaan cocok', tahap='safety', no_wa='6281000000002', perusahaan=self.perusahaan)
        Pic.objects.create(nama='Keduanya', tahap='safety', no_wa='6281000000003', site=self.lmo,
                           perusahaan=self.perusahaan)
        Pic.objects.create(nama='Site lain', tahap='safety', no_wa='6281000000004', site=self.smo)
        Pic.objects.create(nama='Perusahaan lain', tahap='safety', no_wa='6281000000005', perusahaan=other)
        Pic.objects.create(nama='Tahap lain', tahap='sgi', no_wa='6281000000006')
        Pic.objects.create(nama='Nonaktif', tahap='safety', no_wa='6281000000007', aktif=False)
        self.notify(self.request('menunggu_safety'))
        # The closest of those covering it; the site and company PICs are its backup.
        self.assertEqual(set(Notifikasi.objects.values_list('tujuan', flat=True)), {'6281000000003'})

    def test_return_to_it_tells_the_it_pic_and_the_submitter(self):
        Pic.objects.create(nama='IT LMO', tahap='it', no_wa='6286666666666', site=self.lmo)
        self.notify(self.request('return_it'))
        self.assertEqual(set(Notifikasi.objects.values_list('tujuan', flat=True)),
                         {'6286666666666', 'it@example.com'})

    def test_survey_mitra_pic_replaces_mitra_users(self):
        Pic.objects.create(nama='Mitra LMO', tahap='mitra', no_wa='6287777777777', site=self.lmo,
                           perusahaan=self.perusahaan)
        self.notify(self.request('menunggu_mitra'))
        self.assertEqual(list(Notifikasi.objects.values_list('tujuan', flat=True)), ['6287777777777'])

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

    def test_approved_tells_every_stage_and_the_submitter(self):
        Pic.objects.create(nama='Mitra', tahap='mitra', no_wa='6287000000001', site=self.lmo)
        Pic.objects.create(nama='SGI', tahap='sgi', no_wa='6287000000002')
        Pic.objects.create(nama='SGI SMO', tahap='sgi', no_wa='6287000000003', site=self.smo)
        self.notify(self.request('approved', feedback_safety='Sudah sesuai'))
        # Safety has no PIC, so its role holder is told; IT has none either, so only the submitter.
        self.assertEqual(set(Notifikasi.objects.values_list('tujuan', flat=True)),
                         {'6287000000001', '6287000000002', '6281233333333', 'it@example.com'})
        sent = Notifikasi.objects.filter(kanal='wa').first()
        self.assertIn('*Approved*', sent.pesan)
        self.assertIn('Feedback Safety: Sudah sesuai', sent.pesan)
        self.assertIn('sudah sah dan berlaku', sent.pesan)

    def test_approved_with_warning_lists_what_is_missing(self):
        self.notify(self.request('approved_warning', feedback_safety='Perlu dibersihkan',
                                 checklist_safety=['kamera_online']))
        pesan = Notifikasi.objects.first().pesan
        self.assertIn('Belum terpenuhi: Lensa bersih, tidak terhalang;', pesan)
        self.assertNotIn('Kamera online', pesan)

    def test_person_in_several_stages_gets_one_message(self):
        for tahap in ('sgi', 'safety'):
            Pic.objects.create(nama='Satu orang', tahap=tahap, no_wa='6287000000009')
        self.notify(self.request('approved', feedback_safety='Ok'))
        self.assertEqual(Notifikasi.objects.filter(tujuan='6287000000009').count(), 1)

    def test_every_step_tells_someone(self):
        for status in services.TODO:
            with self.subTest(status):
                Notifikasi.objects.all().delete()
                PengajuanCoverage.objects.all().delete()
                self.assertTrue(self.notify(self.request(status)))

    def test_step_nobody_can_receive_warns_the_user(self):
        site = Site.objects.create(kode='BMO')
        cctv = Cctv.objects.create(no_cctv='BMO-1', nama='X', site=site, perusahaan=self.perusahaan)
        pengajuan = PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status='menunggu_sgi',
                                                     minggu=datetime.date(2026, 11, 2))
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get(name=roles.ADMIN))
        self.client.force_login(admin)
        response = self.client.post(reverse('cctv:pengajuan_sgi', args=[pengajuan.pk]),
                                    {'aksi': 'mitra', 'catatan': 'Geser'}, follow=True)
        self.assertContains(response, 'Belum ada penerima notifikasi')

    def test_failed_delivery_is_logged_and_can_be_resent(self):
        self.urlopen.return_value = fonnte_reply({'status': False, 'reason': 'invalid token'})
        self.notify(self.request('menunggu_safety'))
        failed = Notifikasi.objects.get()
        self.assertEqual(failed.status, 'gagal')
        self.assertIn('invalid token', failed.error)

        self.urlopen.return_value = fonnte_reply(FONNTE_OK)
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get(name=roles.ADMIN))
        self.client.force_login(admin)
        self.client.post(reverse('notifikasi:resend', args=[failed.pk]))
        failed.refresh_from_db()
        self.assertEqual((failed.status, failed.percobaan), ('terkirim', 2))

    def test_unreachable_fonnte_does_not_break_the_flow(self):
        self.urlopen.side_effect = channels.urllib.error.URLError('no route')
        Pic.objects.create(nama='SGI', tahap='sgi', no_wa='6284444444444', email='sgi@example.com')
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
        self.admin.groups.add(Group.objects.get(name=roles.ADMIN))
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
            self.client.post(reverse('notifikasi:pic_list'), {'sid': 's81ut', 'tahap': 'sgi', 'aktif': 'on'})
        pic = Pic.objects.get()
        self.assertEqual((pic.karyawan, pic.nama, pic.email, pic.no_wa),
                         (self.karyawan, 'BUDI SANTOSO', 'budi@beraucoal.co.id', '6281234567890'))

    def test_typed_number_overrides_sid_and_unknown_sid_is_refused(self):
        self.client.post(reverse('notifikasi:pic_list'),
                         {'sid': 'S81UT', 'tahap': 'sgi', 'no_wa': '0899 1111 2222', 'aktif': 'on'})
        self.assertEqual(Pic.objects.get().no_wa, '6289911112222')
        response = self.client.post(reverse('notifikasi:pic_list'), {'sid': 'ZZZ99', 'tahap': 'sgi'})
        self.assertContains(response, 'SID tidak ditemukan')

    def test_one_employee_can_hold_several_stages_but_not_the_same_one_twice(self):
        data = {'sid': 'S81UT', 'tahap': 'sgi', 'aktif': 'on'}
        self.client.post(reverse('notifikasi:pic_list'), data)
        response = self.client.post(reverse('notifikasi:pic_list'), data)
        self.assertContains(response, 'sudah menjadi PIC')
        self.client.post(reverse('notifikasi:pic_list'), {**data, 'tahap': 'safety'})
        self.assertEqual(set(Pic.objects.values_list('tahap', flat=True)), {'sgi', 'safety'})

    def test_page_lists_every_stage(self):
        self.client.post(reverse('notifikasi:pic_list'), {'sid': 'S81UT', 'tahap': 'sgi', 'aktif': 'on'})
        page = self.client.get(reverse('notifikasi:pic_list'))
        self.assertEqual([(label, count) for _, label, count in page.context['tabs']],
                         [('IT', 0), ('Survey Mitra', 0), ('Survey BC (SGI)', 1), ('Safety BC', 0),
                          ('Mining (Area Kerja)', 0)])
        self.assertEqual((page.context['tab'], page.context['pics']), ('it', []))
        self.assertContains(page, 'Belum ada PIC IT')

        page = self.client.get(reverse('notifikasi:pic_list'), {'tab': 'sgi'})
        self.assertEqual(len(page.context['pics']), 1)
        self.assertContains(page, 'belum pernah login')
        self.assertEqual(page.context['form'].initial['tahap'], 'sgi')

    def test_unknown_tab_opens_the_first_stage(self):
        page = self.client.get(reverse('notifikasi:pic_list'), {'tab': 'x'})
        self.assertEqual(page.context['tab'], 'it')

    def test_actions_return_to_the_tab_of_the_stage(self):
        url = reverse('notifikasi:pic_list')
        response = self.client.post(url, {'sid': 'S81UT', 'tahap': 'safety', 'aktif': 'on'})
        self.assertRedirects(response, f'{url}?tab=safety')
        pic = Pic.objects.get()
        response = self.client.post(reverse('notifikasi:pic_test', args=[pic.pk]))
        self.assertRedirects(response, f'{url}?tab=safety')
        # A refused assignment stays on the stage it was for.
        page = self.client.post(url, {'sid': 'ZZZ99', 'tahap': 'mitra'})
        self.assertEqual(page.context['tab'], 'mitra')

    def test_add_edit_test_and_delete(self):
        self.client.post(reverse('notifikasi:pic_list'), {'sid': 'S81UT', 'tahap': 'sgi', 'aktif': 'on'})
        pic = Pic.objects.get()
        self.assertEqual(pic.no_wa, '6281234567890')

        self.client.post(reverse('notifikasi:pic_update', args=[pic.pk]),
                         {'sid': 'S81UT', 'tahap': 'sgi', 'no_wa': '', 'email': 'b@example.com'})
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
        self.assertFalse(Pic.objects.exists())
        self.assertFalse(Notifikasi.objects.filter(pic__isnull=False).exists())  # the log stays, unlinked
        self.assertTrue(Notifikasi.objects.exists())

    def test_send_wa_needs_a_token(self):
        with override_settings(FONNTE_TOKEN=''):
            with self.assertRaisesMessage(channels.DeliveryError, 'FONNTE_TOKEN'):
                channels.send_wa('6281234567890', 'x')


class PicAccessTests(TestCase):
    """Assigning a PIC gives the stage's access once the employee has a login; removing takes it back."""

    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal')
        cls.cctv_lmo = Cctv.objects.create(no_cctv='LMO-1', nama='A', site=cls.lmo, perusahaan=cls.perusahaan)
        cls.cctv_smo = Cctv.objects.create(no_cctv='SMO-1', nama='B', site=cls.smo, perusahaan=cls.perusahaan)
        cls.user = get_user_model().objects.create_user('budi', password='x')
        cls.karyawan = Karyawan.objects.create(sid='S81UT', nama='BUDI', user=cls.user)

    def scope(self):
        return get_scope(get_user_model().objects.get(pk=self.user.pk))

    def assign(self, tahap, **extra):
        return Pic.objects.create(karyawan=self.karyawan, nama='BUDI', tahap=tahap, email='b@example.com', **extra)

    def test_nothing_without_an_assignment(self):
        self.assertFalse(self.scope().can_open)

    def test_survey_bc_and_safety_for_all_sites_work_on_every_site(self):
        self.assign('sgi')
        self.assign('safety')
        scope = self.scope()
        for cctv in (self.cctv_lmo, self.cctv_smo):
            self.assertTrue(scope.can_work('sgi', cctv) and scope.can_work('safety', cctv))
        self.assertTrue(scope.can_open)
        self.assertEqual(scope.filter(Cctv.objects.all()).count(), 2)

    def test_survey_mitra_digitises_within_their_site_only(self):
        self.assign('mitra', site=self.lmo)
        scope = self.scope()
        self.assertTrue(scope.can_digitize(self.cctv_lmo))
        self.assertFalse(scope.can_digitize(self.cctv_smo))
        self.assertFalse(scope.can_edit(self.cctv_lmo))

    def test_it_changes_within_their_site_without_connection_details(self):
        self.assign('it', site=self.lmo)
        scope = self.scope()
        self.assertTrue(scope.can_create and scope.can_edit(self.cctv_lmo))
        self.assertFalse(scope.can_edit(self.cctv_smo))
        self.assertFalse(scope.can_see_akses)
        self.assertFalse(scope.can_delete(self.cctv_lmo))

    def test_inactive_or_removed_pic_loses_the_access(self):
        pic = self.assign('sgi', aktif=False)
        self.assertFalse(self.scope().can_work('sgi', self.cctv_lmo))
        Pic.objects.filter(pk=pic.pk).update(aktif=True)
        self.assertTrue(self.scope().can_work('sgi', self.cctv_lmo))
        pic.delete()
        self.assertFalse(self.scope().can_work('sgi', self.cctv_lmo))

    def test_stepper_names_the_pic_of_each_stage(self):
        self.assign('sgi')
        Pic.objects.create(nama='Bukan site ini', tahap='safety', email='x@example.com', site=self.smo)
        pengajuan = PengajuanCoverage.objects.create(cctv=self.cctv_lmo, jenis='dipindah', status='menunggu_sgi',
                                                     minggu=datetime.date(2026, 11, 2))
        self.client.force_login(self.user)
        page = self.client.get(reverse('cctv:pengajuan', args=[pengajuan.pk]))
        self.assertContains(page, 'PIC: BUDI')
        self.assertNotContains(page, 'Bukan site ini')


class PicFormTests(TestCase):
    def test_form_offers_only_perusahaan_with_cctv(self):
        site = Site.objects.create(kode='LMO')
        used = Perusahaan.objects.create(nama='PT Punya CCTV')
        unused = Perusahaan.objects.create(nama='PT Tanpa CCTV')
        Cctv.objects.bulk_create([Cctv(no_cctv='X-1', nama='X', site=site, perusahaan=used)])
        offered = PicForm().fields['perusahaan'].queryset
        self.assertIn(used, offered)
        self.assertNotIn(unused, offered)
