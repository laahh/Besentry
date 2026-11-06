from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point
from django.test import TestCase
from django.urls import reverse

from areakerja import access as area_access
from areakerja.models import Departemen, MitraSite, PengajuanAreaKerja
from cctv.access import get_scope
from cctv.models import CakupanCctv, Cctv, PengajuanCoverage
from cctv.pengajuan_views import _can_mitra, _can_safety, _can_sgi
from cctv.tasklist_common import covering
from core.models import Karyawan, Perusahaan, Site
from navigation import roles

from . import services
from .models import Pic, most_specific

MONDAY = date(2026, 10, 26)


class PerSiteBase(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.fad = Perusahaan.objects.create(nama='PT Fajar', kode='FAD')
        cls.kdc = Perusahaan.objects.create(nama='PT KDC', kode='KDC')
        cls.bc = Perusahaan.objects.create(nama='PT Berau Coal', kode=Perusahaan.KODE_BERAU_COAL)
        cls.cctv_lmo = cls.cctv('LMO-1', cls.lmo, cls.fad)
        cls.cctv_lmo_kdc = cls.cctv('LMO-2', cls.lmo, cls.kdc)
        cls.cctv_smo = cls.cctv('SMO-1', cls.smo, cls.fad)
        cls.area_lmo = MitraSite.objects.create(site=cls.lmo, perusahaan=cls.fad)
        cls.area_smo = MitraSite.objects.create(site=cls.smo, perusahaan=cls.fad)
        cls.number = 0

    @classmethod
    def cctv(cls, number, site, company):
        return Cctv.objects.create(no_cctv=number, nama='K', site=site, perusahaan=company,
                                   posisi=Point(117.6, 2.2, srid=4326))

    def person(self, sid, tahap, *pic_roles, **place):
        user = get_user_model().objects.create_user(sid.lower(), password='x')
        karyawan = Karyawan.objects.create(sid=sid, nama=sid, user=user)
        pic = Pic.objects.create(karyawan=karyawan, nama=sid, tahap=tahap, email=f'{sid.lower()}@example.com', **place)
        for role in pic_roles:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        return user, pic


class WorkingLimitTests(PerSiteBase):
    def test_review_stages_only_for_the_pic_own_site(self):
        for tahap, status, can in (('sgi', 'menunggu_sgi', _can_sgi), ('safety', 'menunggu_safety', _can_safety)):
            with self.subTest(tahap):
                user, _ = self.person(f'P{tahap}', tahap, site=self.lmo)
                scope = get_scope(user)
                inside = PengajuanCoverage(cctv=self.cctv_lmo, status=status)
                outside = PengajuanCoverage(cctv=self.cctv_smo, status=status)
                self.assertTrue(can(scope, inside))
                self.assertFalse(can(scope, outside))

    def test_pic_of_one_company_only_works_on_that_company(self):
        user, _ = self.person('PF', 'sgi', site=self.lmo, perusahaan=self.fad)
        scope = get_scope(user)
        self.assertTrue(_can_sgi(scope, PengajuanCoverage(cctv=self.cctv_lmo, status='menunggu_sgi')))
        self.assertFalse(_can_sgi(scope, PengajuanCoverage(cctv=self.cctv_lmo_kdc, status='menunggu_sgi')))

    def test_pic_may_still_see_other_sites(self):
        user, _ = self.person('PV', 'sgi', site=self.lmo)
        scope = get_scope(user)
        self.assertEqual(scope.filter(Cctv.objects.all()).count(), 3)
        self.assertTrue(scope.allows(self.smo.pk, self.fad.pk))

    def test_posting_a_review_for_another_site_is_refused(self):
        user, _ = self.person('PR', 'sgi', site=self.lmo)
        pengajuan = PengajuanCoverage.objects.create(cctv=self.cctv_smo, jenis='dipindah', status='menunggu_sgi')
        self.client.force_login(user)
        response = self.client.post(reverse('cctv:pengajuan_sgi', args=[pengajuan.pk]),
                                    {'aksi': 'mitra', 'catatan': 'x'})
        self.assertEqual(response.status_code, 403)
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'menunggu_sgi')

    def test_page_of_another_site_opens_without_the_work_form(self):
        user, _ = self.person('PO', 'sgi', site=self.lmo)
        pengajuan = PengajuanCoverage.objects.create(cctv=self.cctv_smo, jenis='dipindah', status='menunggu_sgi')
        self.client.force_login(user)
        page = self.client.get(reverse('cctv:pengajuan', args=[pengajuan.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context['can']['sgi'])

    def test_role_holders_work_everywhere(self):
        user = get_user_model().objects.create_user('role', password='x')
        user.groups.add(Group.objects.get_or_create(name=roles.SURVEY_BC)[0])
        scope = get_scope(user)
        self.assertTrue(_can_sgi(scope, PengajuanCoverage(cctv=self.cctv_smo, status='menunggu_sgi')))

    def test_admin_works_everywhere(self):
        user = get_user_model().objects.create_user('adm', password='x')
        user.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        self.assertTrue(get_scope(user).can_work('safety', self.cctv_smo))

    def test_being_it_pic_elsewhere_does_not_open_digitising_here(self):
        user, _ = self.person('PX', 'it', site=self.smo)
        Pic.objects.create(karyawan=user.karyawan, nama='PX', tahap='mitra', email='px@example.com', site=self.lmo)
        scope = get_scope(user)
        self.assertTrue(scope.can_digitize(self.cctv_lmo))
        self.assertFalse(scope.can_digitize(self.cctv_smo))
        self.assertFalse(_can_mitra(scope, PengajuanCoverage(cctv=self.cctv_smo, status='menunggu_mitra')))

    def test_survey_mitra_role_stays_within_its_coverage(self):
        user = get_user_model().objects.create_user('mitra', password='x')
        role = Group.objects.create(name='CCTV LMO')
        CakupanCctv.objects.create(group=role, site=self.lmo)
        user.groups.add(Group.objects.get_or_create(name=roles.SURVEY_MITRA)[0], role)
        scope = get_scope(user)
        self.assertTrue(scope.can_digitize(self.cctv_lmo))
        self.assertFalse(scope.can_digitize(self.cctv_smo))


class AreaLimitTests(PerSiteBase):
    def test_department_only_at_the_pic_own_site(self):
        user, _ = self.person('PA', 'mining', site=self.lmo)
        scope = get_scope(user)
        self.assertEqual(area_access.departemen(scope, self.area_lmo), [Departemen.MINING.value])
        self.assertEqual(area_access.departemen(scope, self.area_smo), [])

    def test_verifying_another_site_is_refused(self):
        user, _ = self.person('PB', 'safety', site=self.lmo)
        pengajuan = PengajuanAreaKerja.objects.create(mitra_site=self.area_smo, minggu=MONDAY,
                                                      status=PengajuanAreaKerja.Status.MENUNGGU_SGI)
        self.client.force_login(user)
        response = self.client.post(reverse('areakerja:verifikasi', args=[pengajuan.pk]),
                                    {'departemen': 'safety', 'aksi': 'setujui'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(pengajuan.verifikasi.exists())

    def test_tasklist_of_a_site_pic_stops_at_their_site(self):
        user, _ = self.person('PC', 'mining', site=self.lmo)
        mine = PengajuanAreaKerja.objects.create(mitra_site=self.area_lmo, minggu=MONDAY,
                                                 status=PengajuanAreaKerja.Status.MENUNGGU_SGI)
        PengajuanAreaKerja.objects.create(mitra_site=self.area_smo, minggu=MONDAY,
                                          status=PengajuanAreaKerja.Status.MENUNGGU_SGI)
        self.client.force_login(user)
        page = self.client.get('/cctv/tasklist/', {'jenis': 'area'})
        self.assertEqual([row['p'].pk for row in page.context['rows']], [mine.pk])

    def test_survey_bc_uploads_berau_coal_only_where_assigned(self):
        bc_lmo = MitraSite.objects.create(site=self.lmo, perusahaan=self.bc)
        bc_smo = MitraSite.objects.create(site=self.smo, perusahaan=self.bc)
        user, _ = self.person('PD', 'sgi', site=self.lmo)
        scope = get_scope(user)
        self.assertTrue(area_access.can_upload(scope, bc_lmo))
        self.assertFalse(area_access.can_upload(scope, bc_smo))


class MostSpecificTests(PerSiteBase):
    def assign(self, nama, **place):
        return Pic.objects.create(nama=nama, tahap='sgi', email=f'{nama}@example.com', **place)

    def test_order_is_company_in_site_then_company_then_site_then_all(self):
        everything = self.assign('semua')
        self.assertEqual(most_specific([everything]), [everything])
        site = self.assign('site', site=self.lmo)
        self.assertEqual(most_specific([everything, site]), [site])
        company = self.assign('pt', perusahaan=self.fad)
        self.assertEqual(most_specific([everything, site, company]), [company])
        both = self.assign('dua', site=self.lmo, perusahaan=self.fad)
        self.assertEqual(most_specific([everything, site, company, both]), [both])
        self.assertEqual(most_specific([]), [])

    def test_two_pics_equally_specific_both_hold_it(self):
        first, second = self.assign('a', site=self.lmo), self.assign('b', site=self.lmo)
        self.assertCountEqual(most_specific([first, second]), [first, second])

    def test_company_pic_is_told_and_site_pic_is_only_the_backup(self):
        site = self.assign('site', site=self.lmo)
        company = self.assign('pt', site=self.lmo, perusahaan=self.fad)
        self.assertEqual(services.pics_for(self.cctv_lmo, 'sgi'), [company])
        self.assertEqual(services.pics_for(self.cctv_lmo_kdc, 'sgi'), [site])
        self.assertEqual(covering([site, company], 'sgi', self.cctv_lmo), [company])

    def test_most_specific_applies_per_stage(self):
        self.assign('site', site=self.lmo)
        safety = Pic.objects.create(nama='pusat', tahap='safety', email='p@example.com')
        names = {pic.nama for pic in services.pics_for(self.cctv_lmo)}
        self.assertEqual(names, {'site', safety.nama})

    def test_task_goes_to_the_company_pic_not_the_site_pic(self):
        site_user, _ = self.person('SI', 'sgi', site=self.lmo)
        company_user, _ = self.person('PT', 'sgi', site=self.lmo, perusahaan=self.fad)
        pengajuan = PengajuanCoverage.objects.create(cctv=self.cctv_lmo, jenis='dipindah', status='menunggu_sgi')
        backup = PengajuanCoverage.objects.create(cctv=self.cctv_lmo_kdc, jenis='dipindah', status='menunggu_sgi')
        self.client.force_login(company_user)
        self.assertEqual([r['p'].pk for r in self.client.get('/cctv/tasklist/', {'jenis': 'cctv'}).context['rows']],
                         [pengajuan.pk])
        self.client.force_login(site_user)
        self.assertEqual([r['p'].pk for r in self.client.get('/cctv/tasklist/', {'jenis': 'cctv'}).context['rows']],
                         [backup.pk])
        self.assertTrue(_can_sgi(get_scope(site_user), pengajuan))  # the backup may still step in


class MasterPicPageTests(PerSiteBase):
    def setUp(self):
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        self.client.force_login(admin)

    def open_tab(self, tab):
        return self.client.get(reverse('notifikasi:pic_list'), {'tab': tab})

    def test_lists_sites_and_companies_without_a_pic(self):
        gaps = self.open_tab('sgi').context['tanpa_pic']
        self.assertEqual(gaps['jumlah'], 2)
        self.assertEqual(gaps['total'], 2)
        self.assertEqual([(g['site'], g['perusahaan']) for g in gaps['site']],
                         [('LMO', ['PT Fajar']), ('SMO', ['PT Fajar'])])

    def test_pics_cover_their_place(self):
        Pic.objects.create(nama='a', tahap='sgi', email='a@example.com', site=self.lmo)
        response = self.open_tab('sgi')
        self.assertEqual([g['site'] for g in response.context['tanpa_pic']['site']], ['SMO'])
        self.assertContains(response, '1 dari 2')

    def test_a_pic_for_all_sites_covers_everything(self):
        Pic.objects.create(nama='a', tahap='sgi', email='a@example.com')
        response = self.open_tab('sgi')
        self.assertEqual(response.context['tanpa_pic']['jumlah'], 0)
        self.assertContains(response, 'Semua site dan perusahaan sudah punya PIC')

    def test_inactive_pic_does_not_count_and_other_stages_do_not_either(self):
        Pic.objects.create(nama='a', tahap='sgi', email='a@example.com', aktif=False)
        Pic.objects.create(nama='b', tahap='safety', email='b@example.com')
        self.assertEqual(self.open_tab('sgi').context['tanpa_pic']['jumlah'], 2)

    def test_survey_mitra_tab_skips_berau_coal(self):
        MitraSite.objects.create(site=self.lmo, perusahaan=self.bc)
        self.assertEqual(self.open_tab('mitra').context['tanpa_pic']['total'], 2)
        self.assertEqual(self.open_tab('sgi').context['tanpa_pic']['total'], 3)

    def test_inactive_mitra_site_is_left_out(self):
        MitraSite.objects.filter(pk=self.area_smo.pk).update(aktif=False)
        self.assertEqual(self.open_tab('sgi').context['tanpa_pic']['total'], 1)

    def test_rows_are_ordered_by_site_with_all_sites_first(self):
        Pic.objects.create(nama='z', tahap='sgi', email='z@example.com', site=self.smo)
        Pic.objects.create(nama='y', tahap='sgi', email='y@example.com', site=self.lmo)
        Pic.objects.create(nama='x', tahap='sgi', email='x@example.com')
        self.assertEqual([p.nama for p in self.open_tab('sgi').context['pics']], ['x', 'y', 'z'])

    def test_explains_who_holds_a_task(self):
        self.assertContains(self.open_tab('sgi'), 'paling spesifik')
