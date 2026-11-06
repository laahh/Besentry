from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cctv.coverage import this_monday
from cctv.models import CakupanCctv, Cctv, PengajuanCoverage
from core.models import Karyawan, Perusahaan, Site
from navigation import roles
from notifikasi.models import Pic

from .models import Departemen, MitraSite, PengajuanAreaKerja, RiwayatAreaKerja, VerifikasiAreaKerja

URL = '/cctv/tasklist/'
Status = PengajuanAreaKerja.Status
Aksi = RiwayatAreaKerja.Aksi
LAST_MONDAY = date(2026, 10, 26)


class AreaTasklistBase(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.fad = Perusahaan.objects.create(nama='PT Fajar', kode='FAD')
        cls.bc = Perusahaan.objects.create(nama='PT Berau Coal', kode=Perusahaan.KODE_BERAU_COAL)
        cls.lmo_fad = MitraSite.objects.create(site=cls.lmo, perusahaan=cls.fad)
        cls.smo_fad = MitraSite.objects.create(site=cls.smo, perusahaan=cls.fad)
        cls.lmo_bc = MitraSite.objects.create(site=cls.lmo, perusahaan=cls.bc)
        cls.admin = cls.user('admin', roles.ADMIN)

    @classmethod
    def user(cls, username, *role_names):
        user = get_user_model().objects.create_user(username, password='x')
        for name in role_names:
            user.groups.add(Group.objects.get_or_create(name=name)[0])
        return user

    def mitra_lmo(self):
        user = self.user('mitra', roles.SURVEY_MITRA)
        role = Group.objects.create(name='CCTV LMO FAD')
        CakupanCctv.objects.create(group=role, site=self.lmo, perusahaan=self.fad)
        user.groups.add(role)
        return user

    def request(self, status, mitra_site=None, minggu=LAST_MONDAY, **extra):
        return PengajuanAreaKerja.objects.create(mitra_site=mitra_site or self.lmo_fad, minggu=minggu, status=status,
                                                 **extra)

    def waiting(self, mitra_site=None, hours=5, **extra):
        pengajuan = self.request(Status.MENUNGGU_SGI, mitra_site,
                                 diajukan_pada=timezone.now() - timedelta(hours=hours), **extra)
        self.log(pengajuan, Aksi.DIAJUKAN, hours=hours)
        return pengajuan

    def log(self, pengajuan, aksi, catatan='', **ago):
        entry = RiwayatAreaKerja.objects.create(pengajuan=pengajuan, aksi=aksi, catatan=catatan)
        RiwayatAreaKerja.objects.filter(pk=entry.pk).update(waktu=timezone.now() - timedelta(**ago))

    def approve(self, pengajuan, departemen, user=None, **ago):
        row = VerifikasiAreaKerja.objects.create(pengajuan=pengajuan, departemen=departemen, user=user)
        VerifikasiAreaKerja.objects.filter(pk=row.pk).update(waktu=timezone.now() - timedelta(**ago))

    def open_area(self, user=None, **query):
        self.client.force_login(user or self.admin)
        return self.client.get(URL, {'jenis': 'area', **query})

    def task_ids(self, page):
        return [row['p'].pk for row in page.context['rows']]


class AreaTaskOwnerTests(AreaTasklistBase):
    def test_uploader_gets_their_return_within_their_scope(self):
        mine = self.request(Status.RETURN_MITRA)
        self.request(Status.RETURN_MITRA, self.smo_fad)
        self.assertEqual(self.task_ids(self.open_area(self.mitra_lmo())), [mine.pk])

    def test_a_draft_belongs_to_its_author(self):
        mitra = self.mitra_lmo()
        other = self.user('other', roles.SURVEY_MITRA)
        draft = self.request(Status.DRAFT, diajukan_oleh=mitra)
        self.request(Status.DRAFT, minggu=LAST_MONDAY - timedelta(days=7), diajukan_oleh=other)
        self.assertEqual(self.task_ids(self.open_area(mitra)), [draft.pk])

    def test_each_department_sees_what_still_waits_for_it(self):
        mining = self.user('mining', roles.MINING)
        safety = self.user('safety', roles.SAFETY)
        sgi = self.user('sgi', roles.SURVEY_BC)
        pengajuan = self.waiting()
        self.approve(pengajuan, Departemen.MINING, hours=1)
        self.assertEqual(self.task_ids(self.open_area(mining)), [])
        self.assertEqual(self.task_ids(self.open_area(safety)), [pengajuan.pk])
        self.assertEqual(self.task_ids(self.open_area(sgi)), [pengajuan.pk])

    def test_survey_bc_uploads_for_berau_coal_only(self):
        sgi = self.user('sgi', roles.SURVEY_BC)
        own = self.request(Status.RETURN_MITRA, self.lmo_bc)
        self.request(Status.RETURN_MITRA, self.lmo_fad)
        self.assertEqual(self.task_ids(self.open_area(sgi)), [own.pk])

    def test_assigned_pic_takes_the_department_from_other_role_holders(self):
        pic_user = self.user('budi')
        karyawan = Karyawan.objects.create(sid='BUDI1', nama='BUDI', user=pic_user)
        Pic.objects.create(karyawan=karyawan, nama='BUDI', tahap='sgi', email='b@example.com', site=self.lmo)
        role_holder = self.user('sgi', roles.SURVEY_BC)
        pengajuan = self.waiting()
        self.assertEqual(self.task_ids(self.open_area(pic_user)), [pengajuan.pk])
        self.assertEqual(self.task_ids(self.open_area(role_holder)), [])

    def test_admin_sees_every_task_or_only_their_own(self):
        self.waiting()
        self.assertEqual(len(self.task_ids(self.open_area())), 1)
        self.assertEqual(self.task_ids(self.open_area(lihat='saya')), [])

    def test_approved_requests_are_not_tasks(self):
        self.request(Status.APPROVED)
        self.assertEqual(self.task_ids(self.open_area()), [])

    def test_inactive_mitra_site_is_left_out(self):
        self.waiting()
        MitraSite.objects.filter(pk=self.lmo_fad.pk).update(aktif=False)
        self.assertEqual(self.task_ids(self.open_area()), [])


class AreaTasklistPageTests(AreaTasklistBase):
    def test_row_says_what_to_do_and_links_to_the_request(self):
        pengajuan = self.waiting()
        self.approve(pengajuan, Departemen.SGI, hours=1)
        page = self.open_area()
        row = page.context['rows'][0]
        self.assertEqual(row['judul'], 'LMO - PT Fajar')
        self.assertEqual(row['status'], 'Waiting Verifikasi BC')
        self.assertEqual(row['tugas'], 'Verifikasi area kerja, menunggu Mining, Safety BC')
        self.assertEqual(row['di_tahap'], '5 jam')
        self.assertContains(page, reverse('areakerja:pengajuan', args=[pengajuan.pk]))
        self.assertContains(page, 'Di tahap ini 5 jam')

    def test_progress_shows_each_department(self):
        pengajuan = self.waiting()
        sgi_user = self.user('sgi', roles.SURVEY_BC)
        self.approve(pengajuan, Departemen.SGI, sgi_user, hours=3)
        steps = self.open_area().context['steps']
        self.assertEqual([step['label'] for step in steps], ['Pengajuan area kerja', 'Verifikasi BC', 'Sah & berlaku'])
        self.assertEqual([step['selesai'] for step in steps], [True, False, False])
        detail = steps[1]['detail']
        self.assertEqual(len(detail), 3)
        self.assertIn('SGI: disetujui sgi', detail[0])
        self.assertTrue(detail[0].endswith('2 jam'))
        self.assertEqual(detail[1], 'Mining: menunggu 5 jam')
        self.assertEqual(steps[1]['durasi'], '5 jam')

    def test_berau_coal_progress_names_survey_bc_as_uploader(self):
        self.waiting(self.lmo_bc)
        steps = self.open_area().context['steps']
        self.assertEqual(steps[0]['label'], 'Upload area kerja Survey BC')

    def test_returned_request_shows_the_note_and_keeps_the_time_on_each_stage(self):
        pengajuan = self.request(Status.RETURN_MITRA, diajukan_pada=timezone.now() - timedelta(hours=6))
        PengajuanAreaKerja.objects.filter(pk=pengajuan.pk).update(dibuat=timezone.now() - timedelta(hours=8))
        self.log(pengajuan, Aksi.DIAJUKAN, hours=6)
        self.log(pengajuan, Aksi.DIKEMBALIKAN, 'SGI: batas salah', hours=2)
        page = self.open_area()
        steps = page.context['steps']
        self.assertTrue(steps[0]['dikembalikan'])
        self.assertEqual(steps[0]['catatan'], 'SGI: batas salah')
        self.assertEqual(steps[0]['durasi'], '4 jam')
        self.assertEqual(steps[1]['durasi'], '4 jam')
        self.assertContains(page, 'SGI: batas salah')

    def test_cards_and_stage_table(self):
        late = self.waiting(hours=10)
        self.request(Status.RETURN_MITRA, self.smo_fad)
        self.approve(late, Departemen.SGI, self.user('sgi', roles.SURVEY_BC), hours=4)
        page = self.open_area()
        stats = page.context['statistik']
        self.assertEqual((stats['total'], stats['open'], stats['dikembalikan']), (2, 2, 1))
        self.assertEqual(stats['terlama'], '10 jam')
        self.assertEqual(stats['terlama_nama'], 'LMO · FAD')
        table = {row['key']: row for row in page.context['sla_tahap']}
        self.assertEqual(list(table), ['pengajuan', 'sgi', 'sgi_dept', 'mining', 'safety'])
        self.assertEqual(table['sgi']['open'], 1)
        self.assertEqual(table['sgi_dept']['open'], 0)
        self.assertEqual(table['sgi_dept']['rata_selesai'], '6 jam')
        self.assertEqual(table['mining']['open'], 1)
        self.assertEqual(table['mining']['terlama'], '10 jam')
        self.assertContains(page, 'SLA per tahap')

    def test_imported_approvals_count_in_the_cards_but_not_in_the_averages(self):
        imported = self.request(Status.APPROVED, diajukan_pada=timezone.now() - timedelta(days=10),
                                disetujui_pada=timezone.now() - timedelta(days=10))
        self.log(imported, Aksi.DISETUJUI, 'Impor data awal', hours=1)
        self.approve(imported, Departemen.SGI, hours=1)
        page = self.open_area()
        self.assertEqual(page.context['statistik']['selesai'], 1)
        table = {row['key']: row for row in page.context['sla_tahap']}
        self.assertEqual(table['sgi']['rata_selesai'], '')
        self.assertEqual(table['sgi_dept']['rata_selesai'], '')

    def test_counts_mitra_sites_without_a_request_this_week(self):
        self.request(Status.DRAFT, minggu=this_monday(), diajukan_oleh=self.admin)
        page = self.open_area()
        self.assertEqual(page.context['belum_upload'], 2)
        self.assertContains(page, '2 Mitra Site belum membuat pengajuan')
        self.assertContains(page, reverse('areakerja:index'))

    def test_mitra_only_counts_their_own_mitra_sites(self):
        page = self.open_area(self.mitra_lmo())
        self.assertEqual(page.context['belum_upload'], 1)


class TasklistTabsTests(AreaTasklistBase):
    def cctv_task(self):
        cctv = Cctv.objects.create(no_cctv='LMO-1', nama='Kamera', site=self.lmo, perusahaan=self.fad)
        return PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status='menunggu_sgi')

    def open_page(self, **query):
        self.client.force_login(self.admin)
        return self.client.get(URL, query)

    def test_both_lists_are_counted_and_cctv_comes_first(self):
        self.cctv_task()
        self.waiting()
        self.waiting(self.smo_fad)
        page = self.open_page()
        self.assertEqual(page.context['jenis'], 'cctv')
        self.assertEqual(page.context['jumlah_tugas'], {'cctv': 1, 'area': 2})
        self.assertContains(page, 'Coverage CCTV')
        self.assertContains(page, 'Area Kerja')

    def test_opens_on_work_areas_when_only_those_have_tasks(self):
        self.waiting()
        page = self.open_page()
        self.assertEqual(page.context['jenis'], 'area')
        self.assertEqual(len(page.context['rows']), 1)

    def test_switching_list_keeps_lists_apart(self):
        task = self.cctv_task()
        area = self.waiting()
        self.assertEqual(self.task_ids(self.open_page(jenis='cctv')), [task.pk])
        self.assertEqual(self.task_ids(self.open_page(jenis='area')), [area.pk])

    def test_unknown_list_falls_back(self):
        self.cctv_task()
        self.assertEqual(self.open_page(jenis='x').context['jenis'], 'cctv')

    def test_selecting_a_number_only_looks_in_the_open_list(self):
        task = self.cctv_task()
        area = self.waiting()
        page = self.open_page(jenis='area', p=task.pk)
        self.assertEqual(page.context['selected'], area)

    def test_query_count_does_not_grow_with_the_requests(self):
        self.waiting()
        self.client.force_login(self.admin)
        self.client.get(URL, {'jenis': 'area'})
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as first:
            self.client.get(URL, {'jenis': 'area'})
        self.waiting(self.smo_fad)
        self.waiting(self.lmo_bc)
        with self.assertNumQueries(len(first.captured_queries)):
            self.client.get(URL, {'jenis': 'area'})
