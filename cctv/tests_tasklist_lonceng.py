from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from areakerja.models import MitraSite, PengajuanAreaKerja, VerifikasiAreaKerja
from core.models import Karyawan, Perusahaan, Site
from navigation import roles
from notifikasi.models import Pic

from .models import Cctv, PengajuanCoverage
from .tasklist_views import BELL_LISTED

URL = '/cctv/tasklist/lonceng/'
MONDAY = date(2026, 10, 26)


class BellTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo = Site.objects.create(kode='LMO')
        cls.smo = Site.objects.create(kode='SMO')
        cls.fad = Perusahaan.objects.create(nama='PT Fajar', kode='FAD')
        cls.safety = cls.user('safety', roles.SAFETY)
        cls.admin = cls.user('admin', roles.ADMIN)
        cls.number = 0

    @classmethod
    def user(cls, username, *role_names):
        user = get_user_model().objects.create_user(username, password='x')
        for name in role_names:
            user.groups.add(Group.objects.get_or_create(name=name)[0])
        return user

    def cctv_task(self, status='menunggu_safety', site=None):
        BellTests.number += 1
        cctv = Cctv.objects.create(no_cctv=f'LMO-{self.number}', nama='Kamera', site=site or self.lmo,
                                   perusahaan=self.fad)
        return PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status=status)

    def area_task(self, site=None):
        mitra_site, _ = MitraSite.objects.get_or_create(site=site or self.lmo, perusahaan=self.fad)
        return PengajuanAreaKerja.objects.create(mitra_site=mitra_site, minggu=MONDAY,
                                                 status=PengajuanAreaKerja.Status.MENUNGGU_SGI)

    def open_bell(self, user=None):
        self.client.force_login(user or self.safety)
        return self.client.get(URL)

    def test_lists_my_tasks_of_both_lists(self):
        cctv = self.cctv_task()
        area = self.area_task()
        response = self.open_bell()
        self.assertEqual(response.context['total'], 2)
        self.assertContains(response, 'data-total="2"')
        self.assertContains(response, cctv.no_cctv)
        self.assertContains(response, 'LMO - PT Fajar')
        self.assertContains(response, 'Coverage CCTV')
        self.assertContains(response, 'Area Kerja')
        self.assertContains(response, f'{reverse("cctv:tasklist")}?jenis=cctv&amp;p={cctv.pk}')
        self.assertContains(response, f'{reverse("cctv:tasklist")}?jenis=area&amp;p={area.pk}')

    def test_says_what_to_do_and_how_long_it_has_waited(self):
        self.cctv_task()
        response = self.open_bell()
        self.assertContains(response, 'Review dan beri keputusan Safety')
        self.assertContains(response, 'Di tahap ini')

    def test_a_task_of_another_stage_is_not_mine(self):
        self.cctv_task('menunggu_mitra')
        self.assertEqual(self.open_bell().context['total'], 0)

    def test_a_department_that_approved_is_no_longer_told(self):
        area = self.area_task()
        VerifikasiAreaKerja.objects.create(pengajuan=area, departemen='safety')
        self.assertEqual(self.open_bell().context['total'], 0)

    def test_admin_sees_only_their_own_tasks_not_every_pic(self):
        self.cctv_task()
        self.area_task()
        self.assertEqual(self.open_bell(self.admin).context['total'], 0)

    def test_admin_who_is_pic_sees_those_tasks(self):
        karyawan = Karyawan.objects.create(sid='ADMIN', nama='ADMIN', user=self.admin)
        Pic.objects.create(karyawan=karyawan, nama='ADMIN', tahap='safety', email='a@example.com')
        self.cctv_task()
        self.assertEqual(self.open_bell(self.admin).context['total'], 1)

    def test_long_list_names_the_newest_and_counts_all(self):
        tasks = [self.cctv_task() for _ in range(BELL_LISTED + 2)]
        response = self.open_bell()
        self.assertEqual(response.context['total'], BELL_LISTED + 2)
        self.assertEqual(len(response.context['rows']), BELL_LISTED)
        self.assertEqual(response.context['rows'][0]['p'].pk, tasks[-1].pk)
        self.assertNotContains(response, f'p={tasks[0].pk}"')
        self.assertContains(response, reverse('cctv:tasklist'))
        self.assertContains(response, 'Lihat semua')

    def test_empty_bell_says_so(self):
        response = self.open_bell()
        self.assertContains(response, 'data-total="0"')
        self.assertContains(response, 'Tidak ada tugas')

    def test_someone_without_cctv_access_gets_an_empty_bell_not_an_error(self):
        response = self.open_bell(self.user('nobody'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 0)
        self.assertNotContains(response, 'Lihat semua')

    def test_needs_login(self):
        self.assertEqual(self.client.get(URL).status_code, 302)

    def test_is_not_cached(self):
        self.assertIn('no-store', self.open_bell()['Cache-Control'])

    def test_names_are_escaped(self):
        cctv = Cctv.objects.create(no_cctv='<b>X</b>', nama='K', site=self.lmo, perusahaan=self.fad)
        PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status='menunggu_safety')
        response = self.open_bell()
        self.assertNotContains(response, '<b>X</b>')
        self.assertContains(response, '&lt;b&gt;X&lt;/b&gt;')

    def test_query_count_does_not_grow_with_the_tasks(self):
        self.cctv_task()
        self.client.force_login(self.safety)
        self.client.get(URL)
        with CaptureQueriesContext(connection) as first:
            self.client.get(URL)
        for _ in range(4):
            self.cctv_task()
        with self.assertNumQueries(len(first.captured_queries)):
            self.client.get(URL)

    def test_every_page_loads_the_bell_without_counting_tasks_itself(self):
        self.client.force_login(self.safety)
        page = self.client.get(reverse('cctv:tasklist'))
        self.assertContains(page, f'data-lonceng-url="{URL}"')
        self.assertContains(page, 'js/lonceng.js')
