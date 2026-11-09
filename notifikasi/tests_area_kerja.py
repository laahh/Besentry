from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from areakerja import services as area_services
from areakerja.models import AreaKerja, Departemen, JenisArea, MitraSite, PengajuanAreaKerja
from cctv.models import CakupanCctv, Cctv, PengajuanCoverage
from core.models import Karyawan, Perusahaan, Site
from navigation import roles

from . import area_kerja, channels
from .models import Notifikasi, Pic

MONDAY = date(2026, 11, 2)
Status = PengajuanAreaKerja.Status


@override_settings(FONNTE_TOKEN='test-token', SITE_URL='https://besentry.example')
class AreaKerjaNotifyBase(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.fad = Perusahaan.objects.create(nama='PT Fajar', kode='FAD')
        cls.bc = Perusahaan.objects.create(nama='PT Berau Coal', kode=Perusahaan.KODE_BERAU_COAL)
        cls.mitra_site = MitraSite.objects.create(site=cls.lmo, perusahaan=cls.fad)
        cls.bc_site = MitraSite.objects.create(site=cls.lmo, perusahaan=cls.bc)
        cls.uploader = get_user_model().objects.create_user('uploader', password='x', email='uploader@example.com')
        cls.approver = get_user_model().objects.create_user('approver', password='x', first_name='Ani')

    def setUp(self):
        patcher = mock.patch.object(channels, 'send_wa')
        self.send_wa = patcher.start()
        self.addCleanup(patcher.stop)

    def pic(self, tahap, nama, **extra):
        slug = nama.lower()
        return Pic.objects.create(tahap=tahap, nama=nama, email=f'{slug}@example.com', **extra)

    def three_departments(self):
        self.pic('sgi', 'Sgi')
        self.pic('mining', 'Mining')
        self.pic('safety', 'Safety')

    def request(self, status=Status.MENUNGGU_SGI, mitra_site=None):
        pengajuan = PengajuanAreaKerja.objects.create(
            mitra_site=mitra_site or self.mitra_site, minggu=MONDAY, status=status, diajukan_oleh=self.uploader,
        )
        for jenis in JenisArea:
            AreaKerja.objects.create(pengajuan=pengajuan, jenis=jenis, sumber=AreaKerja.Sumber.TIDAK_ADA)
        return pengajuan

    def emails(self):
        return set(Notifikasi.objects.filter(kanal='email').values_list('tujuan', flat=True))


class StepTests(AreaKerjaNotifyBase):
    def test_sending_tells_the_three_departments(self):
        self.three_departments()
        self.pic('mitra', 'Mitra')
        pengajuan = self.request(Status.DRAFT)
        with self.captureOnCommitCallbacks(execute=True):
            area_services.ajukan(pengajuan, self.uploader)
        self.assertEqual(self.emails(), {'sgi@example.com', 'mining@example.com', 'safety@example.com'})
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] Waiting Verifikasi BC: LMO - PT Fajar W45 2026')
        self.assertIn('Mohon verifikasi area kerja ini', mail.outbox[0].body)
        link = reverse('areakerja:pengajuan', args=[pengajuan.pk])
        self.assertIn(f'https://besentry.example{link}', mail.outbox[0].body)
        self.assertEqual(set(Notifikasi.objects.values_list('jenis', flat=True)), {Notifikasi.Jenis.AREA_KERJA})

    def test_one_approval_tells_who_is_still_waited_for_and_the_uploader(self):
        self.three_departments()
        pengajuan = self.request()
        with self.captureOnCommitCallbacks(execute=True):
            area_services.verifikasi(pengajuan, self.approver, departemen=Departemen.SGI, aksi='setujui')
        self.assertEqual(self.emails(), {'mining@example.com', 'safety@example.com', 'uploader@example.com'})
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] SGI menyetujui: LMO - PT Fajar W45 2026')
        self.assertIn('Sudah setuju: SGI', mail.outbox[0].body)
        self.assertIn('Menunggu: Mining, Safety BC', mail.outbox[0].body)
        self.assertIn('Oleh: Ani', mail.outbox[0].body)

    def test_last_approval_tells_everyone_once(self):
        self.three_departments()
        self.pic('mitra', 'Mitra')
        pengajuan = self.request()
        area_services.verifikasi(pengajuan, self.approver, departemen=Departemen.SGI, aksi='setujui')
        area_services.verifikasi(pengajuan, self.approver, departemen=Departemen.MINING, aksi='setujui')
        Notifikasi.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True):
            area_services.verifikasi(pengajuan, self.approver, departemen=Departemen.SAFETY, aksi='setujui')
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, Status.APPROVED)
        self.assertEqual(self.emails(), {'sgi@example.com', 'mining@example.com', 'safety@example.com',
                                         'mitra@example.com', 'uploader@example.com'})
        self.assertEqual(Notifikasi.objects.filter(kanal='email').count(), 5)
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] Approved: LMO - PT Fajar W45 2026')

    def test_return_tells_the_uploaders_with_the_note(self):
        self.three_departments()
        self.pic('mitra', 'Mitra')
        pengajuan = self.request()
        with self.captureOnCommitCallbacks(execute=True):
            area_services.verifikasi(pengajuan, self.approver, departemen=Departemen.MINING, aksi='kembalikan',
                                     catatan='batas pit salah')
        self.assertEqual(self.emails(), {'mitra@example.com', 'uploader@example.com'})
        self.assertEqual(mail.outbox[0].subject, '[BeSentry] Return to Mitra: LMO - PT Fajar W45 2026')
        self.assertIn('Catatan: Mining: batas pit salah', mail.outbox[0].body)
        self.assertIn('Mohon perbaiki area kerja', mail.outbox[0].body)

    def test_whatsapp_goes_out_too(self):
        self.pic('sgi', 'Sgi', no_wa='6281111111111')
        pengajuan = self.request(Status.DRAFT)
        with self.captureOnCommitCallbacks(execute=True):
            area_services.ajukan(pengajuan, self.uploader)
        self.send_wa.assert_called_once()
        self.assertEqual(self.send_wa.call_args[0][0], '6281111111111')
        self.assertIn('*Waiting Verifikasi BC*', self.send_wa.call_args[0][1])


class RecipientTests(AreaKerjaNotifyBase):
    def role_user(self, username, *role_names, **extra):
        user = get_user_model().objects.create_user(username, password='x', **extra)
        for name in role_names:
            user.groups.add(Group.objects.get_or_create(name=name)[0])
        return user

    def test_department_without_a_pic_falls_back_to_its_role(self):
        self.pic('sgi', 'Sgi')
        self.role_user('other-sgi', roles.SURVEY_BC, email='othersgi@example.com')
        self.role_user('mining', roles.MINING, email='miningrole@example.com')
        self.role_user('safety', roles.SAFETY, email='safetyrole@example.com')
        area_services.ajukan(self.request(Status.DRAFT), self.uploader)
        self.assertEqual(self.emails(), {'sgi@example.com', 'miningrole@example.com', 'safetyrole@example.com'})

    def test_pic_covers_only_their_site_and_company(self):
        self.pic('sgi', 'Lmo', site=self.lmo)
        self.pic('sgi', 'Smo', site=self.smo)
        self.pic('sgi', 'Bcpic', perusahaan=self.bc)
        area_services.ajukan(self.request(Status.DRAFT), self.uploader)
        self.assertEqual(self.emails(), {'lmo@example.com'})

    def test_return_without_mitra_pic_goes_to_survey_mitra_in_scope(self):
        inside = self.role_user('inside', roles.SURVEY_MITRA, email='inside@example.com')
        outside = self.role_user('outside', roles.SURVEY_MITRA, email='outside@example.com')
        role = Group.objects.create(name='CCTV LMO FAD')
        CakupanCctv.objects.create(group=role, site=self.lmo, perusahaan=self.fad)
        inside.groups.add(role)
        other = Group.objects.create(name='CCTV SMO FAD')
        CakupanCctv.objects.create(group=other, site=self.smo, perusahaan=self.fad)
        outside.groups.add(other)
        area_services.verifikasi(self.request(), self.approver, departemen=Departemen.SGI, aksi='kembalikan',
                                 catatan='x')
        self.assertEqual(self.emails(), {'inside@example.com', 'uploader@example.com'})

    def test_berau_coal_return_goes_to_survey_bc(self):
        self.pic('sgi', 'Sgi')
        self.pic('mitra', 'Mitra')
        area_services.verifikasi(self.request(mitra_site=self.bc_site), self.approver, departemen=Departemen.SAFETY,
                                 aksi='kembalikan', catatan='x')
        self.assertEqual(self.emails(), {'sgi@example.com', 'uploader@example.com'})
        self.assertIn('Return to Survey BC', Notifikasi.objects.first().judul)

    def test_someone_reached_two_ways_gets_one_message(self):
        self.pic('sgi', 'Dua')
        Pic.objects.create(tahap='mining', nama='Dua', email='dua@example.com')
        area_services.ajukan(self.request(Status.DRAFT), self.uploader)
        self.assertEqual(Notifikasi.objects.filter(tujuan='dua@example.com').count(), 1)


class SafetyNetTests(AreaKerjaNotifyBase):
    def test_a_broken_message_does_not_stop_the_flow(self):
        pengajuan = self.request(Status.DRAFT)
        with mock.patch.object(area_kerja, '_message', side_effect=ValueError('boom')):
            area_services.ajukan(pengajuan, self.uploader)
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, Status.MENUNGGU_SGI)
        self.assertEqual(Notifikasi.objects.count(), 0)

    def test_page_warns_when_nobody_can_be_reached(self):
        pengajuan = self.request(Status.DRAFT)
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        self.client.force_login(admin)
        response = self.client.post(reverse('areakerja:ajukan', args=[pengajuan.pk]), follow=True)
        self.assertContains(response, 'Belum ada penerima notifikasi')

    def test_mining_pic_gets_the_mining_task_and_access(self):
        user = get_user_model().objects.create_user('budi', password='x')
        karyawan = Karyawan.objects.create(sid='BUDI1', nama='BUDI', user=user)
        Pic.objects.create(karyawan=karyawan, nama='BUDI', tahap='mining', email='budi@example.com')
        role_holder = get_user_model().objects.create_user('lain', password='x')
        role_holder.groups.add(Group.objects.get_or_create(name=roles.MINING)[0])
        pengajuan = self.request()
        self.client.force_login(user)
        page = self.client.get('/cctv/tasklist/', {'jenis': 'area'})
        self.assertEqual([row['p'].pk for row in page.context['rows']], [pengajuan.pk])
        self.client.force_login(role_holder)
        page = self.client.get('/cctv/tasklist/', {'jenis': 'area'})
        self.assertEqual(page.context['rows'], [])

    def test_mining_pic_is_not_told_about_finished_cctv_requests(self):
        self.pic('mining', 'Mining')
        self.pic('safety', 'Safety')
        cctv = Cctv.objects.create(no_cctv='LMO-1', nama='K', site=self.lmo, perusahaan=self.fad,
                                   posisi=Point(117.6, 2.2, srid=4326))
        pengajuan = PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status='approved',
                                                     feedback_safety='ok')
        from . import services
        emails = {email for _, _, email in services.recipients(pengajuan)}
        self.assertIn('safety@example.com', emails)
        self.assertNotIn('mining@example.com', emails)

    def test_master_pic_has_a_mining_tab(self):
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        self.client.force_login(admin)
        response = self.client.get(reverse('notifikasi:pic_list'), {'tab': 'mining'})
        self.assertContains(response, 'Belum ada PIC Mining')
