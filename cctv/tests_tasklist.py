from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from core.models import Karyawan, Perusahaan, Site
from navigation import roles
from navigation.menu import build_menu
from notifikasi.models import Pic

from .models import CakupanCctv, Cctv, PengajuanCoverage, RiwayatPengajuan

URL = '/cctv/tasklist/'


class TasklistTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal')
        cls.number = 0

    def cctv(self, site=None):
        TasklistTests.number += 1
        return Cctv.objects.create(no_cctv=f'CCTV-{self.number}', nama='Kamera', site=site or self.lmo,
                                   perusahaan=self.perusahaan)

    def request(self, status, site=None, **extra):
        return PengajuanCoverage.objects.create(cctv=self.cctv(site), jenis='dipindah', status=status, **extra)

    def user(self, username, *role_names):
        user = get_user_model().objects.create_user(username, password='x')
        for name in role_names:
            user.groups.add(Group.objects.get_or_create(name=name)[0])
        return user

    def pic(self, user, tahap, **extra):
        karyawan, _ = Karyawan.objects.get_or_create(sid=user.username[:5].upper(),
                                                     defaults={'nama': user.username, 'user': user})
        return Pic.objects.create(karyawan=karyawan, nama=user.username, tahap=tahap, email='a@example.com', **extra)

    def open_page(self, user, **query):
        self.client.force_login(user)
        return self.client.get(URL, query)

    def task_ids(self, page):
        return [row['p'].pk for row in page.context['rows']]

    def test_pic_sees_their_stage_within_their_coverage(self):
        budi = self.user('budi')
        self.pic(budi, 'sgi', site=self.lmo)
        mine = self.request('menunggu_sgi')
        self.request('menunggu_sgi', site=self.smo)
        self.request('menunggu_safety')
        self.assertEqual(self.task_ids(self.open_page(budi)), [mine.pk])

    def test_stage_with_a_pic_is_not_a_task_of_other_role_holders(self):
        other = self.user('other', roles.SURVEY_BC)
        self.pic(self.user('budi'), 'sgi')
        self.request('menunggu_sgi')
        self.assertEqual(self.task_ids(self.open_page(other)), [])

    def test_stage_without_a_pic_is_a_task_of_its_role_holders(self):
        pengajuan = self.request('menunggu_safety')
        self.request('menunggu_sgi')
        safety = self.user('safety', roles.SAFETY)
        self.assertEqual(self.task_ids(self.open_page(safety)), [pengajuan.pk])

    def test_survey_mitra_role_only_gets_cctv_in_their_scope(self):
        role = Group.objects.create(name='CCTV LMO')
        CakupanCctv.objects.create(group=role, site=self.lmo, perusahaan=self.perusahaan)
        mitra = self.user('mitra', roles.SURVEY_MITRA)
        mitra.groups.add(role)
        mine = self.request('menunggu_mitra')
        self.request('menunggu_mitra', site=self.smo)
        self.assertEqual(self.task_ids(self.open_page(mitra)), [mine.pk])

    def test_it_keeps_their_return_and_draft_while_the_draft_stays_private(self):
        author, it_pic = self.user('author'), self.user('itpic')
        role = Group.objects.create(name='CCTV LMO ubah')
        CakupanCctv.objects.create(group=role, site=self.lmo, perusahaan=self.perusahaan, bisa_ubah=True)
        author.groups.add(role)
        self.pic(it_pic, 'it')
        returned = self.request('return_it', diajukan_oleh=author)
        draft = self.request('draft_it', diajukan_oleh=author)
        self.assertCountEqual(self.task_ids(self.open_page(author)), [returned.pk, draft.pk])
        self.assertEqual(self.task_ids(self.open_page(it_pic)), [returned.pk])

    def test_draft_of_a_new_cctv_is_listed_for_its_author(self):
        author = self.user('author')
        role = Group.objects.create(name='CCTV ubah')
        CakupanCctv.objects.create(group=role, bisa_ubah=True)
        author.groups.add(role)
        draft = PengajuanCoverage.objects.create(jenis='baru', diajukan_oleh=author,
                                                 data_draft={'no_cctv': ['LMO-BARU']})
        page = self.open_page(author)
        self.assertEqual(self.task_ids(page), [draft.pk])
        self.assertContains(page, 'LMO-BARU')

    def test_finished_requests_are_not_tasks(self):
        self.request('approved')
        admin = self.user('admin', roles.ADMIN)
        self.assertEqual(self.task_ids(self.open_page(admin)), [])

    def test_admin_sees_every_pic_or_only_their_own(self):
        admin = self.user('admin', roles.ADMIN)
        self.pic(admin, 'sgi')
        mine = self.request('menunggu_sgi')
        other = self.request('menunggu_safety')
        self.assertCountEqual(self.task_ids(self.open_page(admin)), [mine.pk, other.pk])
        self.assertEqual(self.task_ids(self.open_page(admin, lihat='saya')), [mine.pk])

    def test_other_users_cannot_widen_to_every_pic(self):
        budi = self.user('budi')
        self.pic(budi, 'sgi', site=self.lmo)
        self.request('menunggu_safety')
        self.assertEqual(self.task_ids(self.open_page(budi, lihat='semua')), [])

    def test_page_has_no_stage_tabs_and_only_admins_can_switch_view(self):
        admin = self.user('admin', roles.ADMIN)
        budi = self.user('budi')
        self.pic(budi, 'sgi')
        self.request('menunggu_sgi')
        page = self.open_page(admin)
        self.assertNotContains(page, 'nav-tabs')
        self.assertContains(page, 'Semua PIC')
        self.assertNotContains(self.open_page(budi), 'Semua PIC')

    def test_tasks_are_grouped_per_stage_longest_waiting_first(self):
        admin = self.user('admin', roles.ADMIN)
        first_sgi = self.request('menunggu_sgi')
        mitra = self.request('return_mitra')
        second_sgi = self.request('menunggu_sgi')
        groups = self.open_page(admin).context['groups']
        self.assertEqual([g['key'] for g in groups], ['mitra', 'sgi'])
        self.assertEqual([r['p'].pk for r in groups[0]['rows']], [mitra.pk])
        self.assertEqual([r['p'].pk for r in groups[1]['rows']], [first_sgi.pk, second_sgi.pk])

    def test_only_the_selected_stage_starts_open(self):
        admin = self.user('admin', roles.ADMIN)
        mitra = self.request('menunggu_mitra')
        sgi = self.request('menunggu_sgi')
        page = self.open_page(admin)
        self.assertEqual(page.context['selected'], mitra)
        self.assertEqual([g['buka'] for g in page.context['groups']], [True, False])
        page = self.open_page(admin, p=sgi.pk)
        self.assertEqual([g['buka'] for g in page.context['groups']], [False, True])

    def test_upload_many_is_offered_to_survey_mitra_only(self):
        self.request('menunggu_mitra')
        admin = self.user('admin', roles.ADMIN)
        self.assertContains(self.open_page(admin), 'Upload SHP sekaligus')
        self.request('menunggu_sgi')
        budi = self.user('budi')
        self.pic(budi, 'sgi')
        self.assertNotContains(self.open_page(budi), 'Upload SHP sekaligus')

    def test_progress_follows_the_selected_task(self):
        admin = self.user('admin', roles.ADMIN)
        self.pic(admin, 'safety')
        first = self.request('menunggu_mitra')
        second = self.request('menunggu_safety')
        RiwayatPengajuan.objects.create(pengajuan=second, aksi='setuju_sgi', user=admin)

        page = self.open_page(admin)
        self.assertEqual(page.context['selected'], first)
        self.assertEqual([s['selesai'] for s in page.context['steps']], [True, False, False, False, False])

        page = self.open_page(admin, p=second.pk)
        steps = page.context['steps']
        self.assertEqual(page.context['selected'], second)
        self.assertEqual([s['selesai'] for s in steps], [True, True, True, False, False])
        self.assertEqual([s['aktif'] for s in steps], [False, False, False, True, False])
        self.assertEqual(steps[2]['oleh'].user, admin)
        self.assertEqual(steps[3]['pics'], ['admin'])
        self.assertEqual(steps[3]['tugas'], 'Review dan beri keputusan Safety')

    def test_berau_coal_progress_has_no_survey_mitra_stage(self):
        berau_coal = Perusahaan.objects.create(nama='PT Berau Coal BC', kode=Perusahaan.KODE_BERAU_COAL)
        cctv = Cctv.objects.create(no_cctv='LMO-BC-1', nama='Kamera', site=self.lmo, perusahaan=berau_coal)
        PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status='menunggu_sgi')
        page = self.open_page(self.user('admin', roles.ADMIN))
        steps = page.context['steps']
        self.assertEqual([s['label'] for s in steps],
                         ['Input IT', 'Digitasi + Verifikasi Survey BC', 'Review Safety BC', 'Sah & berlaku'])
        self.assertEqual(steps[1]['tugas'], 'Digitasi dan verifikasi boundary coverage')
        self.assertEqual(page.context['rows'][0]['tugas'], 'Digitasi dan verifikasi boundary coverage')

    def test_returned_stage_shows_the_note(self):
        admin = self.user('admin', roles.ADMIN)
        pengajuan = self.request('return_mitra')
        RiwayatPengajuan.objects.create(pengajuan=pengajuan, aksi='dikembalikan', catatan='Geser ke barat')
        page = self.open_page(admin)
        step = page.context['steps'][1]
        self.assertTrue(step['dikembalikan'])
        self.assertEqual(step['catatan'], 'Geser ke barat')
        self.assertContains(page, 'Geser ke barat')

    def test_a_request_outside_the_list_cannot_be_selected(self):
        budi = self.user('budi')
        self.pic(budi, 'sgi')
        mine = self.request('menunggu_sgi')
        foreign = self.request('menunggu_safety')
        page = self.open_page(budi, p=foreign.pk)
        self.assertEqual(page.context['selected'], mine)
        self.assertNotContains(page, foreign.cctv.no_cctv)

    def test_empty_list_renders(self):
        page = self.open_page(self.user('budi', roles.SAFETY))
        self.assertContains(page, 'Tidak ada tugas')
        self.assertEqual(page.context['steps'], [])

    def test_no_cctv_access_is_refused_and_has_no_menu(self):
        nobody = self.user('nobody')
        self.assertEqual(self.open_page(nobody).status_code, 403)
        titles = [item['title'] for section in build_menu(nobody) for item in section['items']]
        self.assertNotIn('Tasklist', titles)

    def test_assigned_pic_gets_the_menu(self):
        budi = self.user('budi')
        self.pic(budi, 'safety')
        titles = [item['title'] for section in build_menu(get_user_model().objects.get(pk=budi.pk))
                  for item in section['items']]
        self.assertIn('Tasklist', titles)
        self.assertEqual(reverse('cctv:tasklist'), URL)
