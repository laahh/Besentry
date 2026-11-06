from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from cctv.access import get_scope
from cctv.models import CakupanCctv, Cctv
from core.models import Perusahaan, Site
from navigation import roles


def formset_data(rows, initial=0):
    data = {'cakupan-TOTAL_FORMS': str(len(rows)), 'cakupan-INITIAL_FORMS': str(initial)}
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f'cakupan-{index}-{key}'] = value
    return data


class RolePageTests(TestCase):
    databases = {'default', 'log'}  # creating a Cctv writes its history to `log`

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user('admin', password='x')
        cls.admin.groups.add(Group.objects.get(name=roles.ADMIN))
        cls.user = User.objects.create_user('budi', password='x', first_name='Budi')
        cls.bmo2 = Site.objects.create(kode='BMO 2')
        cls.pama = Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_only_admins_manage_roles(self):
        self.client.force_login(self.user)
        for url in (reverse('pengaturan:role_list'), reverse('pengaturan:user_list'),
                    reverse('pengaturan:user_roles', args=[self.user.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_create_role_with_scope(self):
        data = {'name': 'CCTV BMO 2 - PAMA'}
        data.update(formset_data([{'site': self.bmo2.pk, 'perusahaan': self.pama.pk, 'bisa_ubah': 'on'}]))
        response = self.client.post(reverse('pengaturan:role_create'), data)
        group = Group.objects.get(name='CCTV BMO 2 - PAMA')
        self.assertRedirects(response, reverse('pengaturan:role_update', args=[group.pk]))
        rule = group.cakupan_cctv.get()
        self.assertEqual((rule.site, rule.perusahaan, rule.bisa_ubah), (self.bmo2, self.pama, True))

    def test_empty_site_means_all_and_duplicates_are_rejected(self):
        row = {'site': '', 'perusahaan': self.pama.pk}
        data = {'name': 'PAMA semua site', **formset_data([row, row])}
        response = self.client.post(reverse('pengaturan:role_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lebih dari sekali')
        self.assertFalse(Group.objects.filter(name='PAMA semua site').exists())

        self.client.post(reverse('pengaturan:role_create'), {'name': 'PAMA semua site', **formset_data([row])})
        rule = CakupanCctv.objects.get(group__name='PAMA semua site')
        self.assertIsNone(rule.site)

    def test_edit_and_remove_scope(self):
        group = Group.objects.create(name='Role A')
        rule = CakupanCctv.objects.create(group=group, site=self.bmo2, perusahaan=self.pama)
        data = {'name': 'Role A', **formset_data(
            [{'id': rule.pk, 'site': self.bmo2.pk, 'perusahaan': self.pama.pk, 'DELETE': 'on'}], initial=1,
        )}
        self.client.post(reverse('pengaturan:role_update', args=[group.pk]), data)
        self.assertFalse(CakupanCctv.objects.filter(group=group).exists())

    def test_system_roles_cannot_be_renamed_or_deleted(self):
        group = Group.objects.get(name=roles.IT)
        rule = group.cakupan_cctv.get()  # full scope from the migration
        data = {'name': 'diganti', **formset_data([{'id': rule.pk, 'site': '', 'perusahaan': ''}], initial=1)}
        self.client.post(reverse('pengaturan:role_update', args=[group.pk]), data)
        group.refresh_from_db()
        self.assertEqual(group.name, roles.IT)

        self.client.post(reverse('pengaturan:role_delete', args=[group.pk]))
        self.assertTrue(Group.objects.filter(name=roles.IT).exists())

    def test_admin_role_page_has_no_scope_rows(self):
        group = Group.objects.get(name=roles.ADMIN)
        response = self.client.get(reverse('pengaturan:role_update', args=[group.pk]))
        self.assertContains(response, 'semua CCTV')
        self.assertIsNone(response.context['formset'])
        response = self.client.post(reverse('pengaturan:role_update', args=[group.pk]), {'name': group.name})
        self.assertEqual(response.status_code, 302)

    def test_delete_custom_role(self):
        group = Group.objects.create(name='Sementara')
        self.client.post(reverse('pengaturan:role_delete', args=[group.pk]))
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())

    def test_generate_pair_roles(self):
        Cctv.objects.create(no_cctv='BMO2-PM-1', nama='A', site=self.bmo2, perusahaan=self.pama)
        self.client.post(reverse('pengaturan:role_generate'), {'bisa_ubah': '1'})
        rule = CakupanCctv.objects.get(group__name='CCTV BMO 2 - PT Pamapersada Nusantara')
        self.assertTrue(rule.bisa_ubah)

    def test_role_list_shows_scope_and_member_count(self):
        group = Group.objects.create(name='CCTV BMO 2 - PAMA')
        CakupanCctv.objects.create(group=group, site=self.bmo2, perusahaan=self.pama)
        self.user.groups.add(group)
        response = self.client.get(reverse('pengaturan:role_list'), {'q': 'bmo 2'})
        self.assertContains(response, 'BMO 2 · PT Pamapersada Nusantara')
        self.assertEqual(response.context['page_obj'][0].jumlah_user, 1)


class UserRolesPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin_group = Group.objects.get(name=roles.ADMIN)
        cls.admin = User.objects.create_user('admin', password='x')
        cls.admin.groups.add(cls.admin_group)
        cls.user = User.objects.create_user('budi', password='x')
        cls.role = Group.objects.create(name='CCTV BMO 2 - PAMA')
        CakupanCctv.objects.create(group=cls.role, site=Site.objects.create(kode='BMO 2'),
                                   perusahaan=Perusahaan.objects.create(nama='PT Pamapersada Nusantara'))

    def setUp(self):
        self.client.force_login(self.admin)

    def test_assign_role_gives_cctv_access(self):
        response = self.client.post(reverse('pengaturan:user_roles', args=[self.user.pk]), {'roles': [self.role.pk]})
        self.assertRedirects(response, reverse('pengaturan:user_list'))
        user = get_user_model().objects.get(pk=self.user.pk)
        self.assertEqual(list(user.groups.all()), [self.role])
        self.assertTrue(get_scope(user).can_open)

        self.client.post(reverse('pengaturan:user_roles', args=[self.user.pk]), {})
        self.assertFalse(get_user_model().objects.get(pk=self.user.pk).groups.exists())

    def test_admin_cannot_remove_own_admin_role(self):
        response = self.client.post(reverse('pengaturan:user_roles', args=[self.admin.pk]), {'roles': [self.role.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.admin.groups.filter(pk=self.admin_group.pk).exists())

    def test_user_list_search_and_role_filter(self):
        self.user.groups.add(self.role)
        response = self.client.get(reverse('pengaturan:user_list'), {'role': self.role.pk})
        self.assertEqual([u.username for u in response.context['page_obj']], ['budi'])
        response = self.client.get(reverse('pengaturan:user_list'), {'q': 'adm'})
        self.assertEqual([u.username for u in response.context['page_obj']], ['admin'])

    def test_menu_shows_settings_to_admin_only(self):
        from navigation.menu import build_menu
        self.assertIn('Pengaturan', [s['title'] for s in build_menu(self.admin)])
        self.assertNotIn('Pengaturan', [s['title'] for s in build_menu(self.user)])
