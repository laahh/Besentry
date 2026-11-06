from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from cctv.models import CakupanCctv
from core.models import Perusahaan, Site

from . import roles

migration = import_module('navigation.migrations.0005_nama_role_standar')


class RoleNameTests(TestCase):
    def test_builtin_roles_have_the_standard_names(self):
        names = set(Group.objects.values_list('name', flat=True))
        self.assertLessEqual({'Admin', 'IT', 'Survey Mitra', 'Survey BC', 'Mining BC', 'Safety BC', 'Mitra'}, names)
        self.assertFalse(names & {'admin-hazard-motion', 'hazard-motion-it-bc', 'hazard-motion-it-mtl',
                                  'hazard-motion-it-pama', 'safety-approval', 'mining-bc', 'mitra-only',
                                  'survey-bc', 'survey-mitra'})

    def test_constants_match_the_database(self):
        self.assertEqual(Group.objects.filter(name__in=roles.ALL_ROLES).count(), len(roles.ALL_ROLES))

    def test_it_keeps_one_rule_covering_everything(self):
        rules = CakupanCctv.objects.filter(group__name=roles.IT)
        self.assertEqual([(r.site_id, r.perusahaan_id, r.bisa_ubah) for r in rules], [(None, None, True)])


class RoleListLabelTests(TestCase):
    def test_roles_that_see_every_cctv_say_so(self):
        admin = get_user_model().objects.create_user('admin', password='x')
        admin.groups.add(Group.objects.get(name=roles.ADMIN))
        self.client.force_login(admin)
        for name in roles.SEES_ALL_ROLES:
            with self.subTest(name):
                page = self.client.get('/pengaturan/role/', {'q': name})
                self.assertContains(page, 'Lihat semua CCTV')
                self.assertNotContains(page, 'Tidak ada akses CCTV')
        self.assertContains(self.client.get('/pengaturan/role/', {'q': roles.SURVEY_MITRA}), 'Tidak ada akses CCTV')


class RoleMigrationTests(TestCase):
    """The migration on data like a database that still has the old names."""

    def setUp(self):
        Group.objects.filter(name__in=roles.ALL_ROLES).delete()
        self.user = get_user_model().objects.create_user('budi', password='x')
        self.lmo = Site.objects.create(kode='LMO-X')
        self.fad = Perusahaan.objects.create(nama='PT Uji', kode='UJI')

    def old_role(self, name, *rules):
        group = Group.objects.create(name=name)
        for rule in rules:
            CakupanCctv.objects.create(group=group, **rule)
        return group

    def forwards(self):
        migration.forwards(apps, None)

    def test_renames_keep_the_members(self):
        self.old_role('survey-bc').user_set.add(self.user)
        self.old_role('safety-approval')
        self.forwards()
        self.assertEqual(list(self.user.groups.values_list('name', flat=True)), ['Survey BC'])
        self.assertFalse(Group.objects.filter(name__in=['survey-bc', 'safety-approval']).exists())
        self.assertTrue(Group.objects.filter(name='Safety BC').exists())

    def test_the_three_it_roles_become_one(self):
        other = get_user_model().objects.create_user('ani', password='x')
        everything = {'site': None, 'perusahaan': None, 'bisa_ubah': True}
        self.old_role('hazard-motion-it-pama', everything).user_set.add(self.user)
        self.old_role('hazard-motion-it-mtl', everything).user_set.add(other)
        self.old_role('hazard-motion-it-bc', everything).user_set.add(self.user)
        self.forwards()
        it = Group.objects.get(name='IT')
        self.assertCountEqual(it.user_set.all(), [self.user, other])
        self.assertEqual(CakupanCctv.objects.filter(group=it).count(), 1)
        self.assertFalse(Group.objects.filter(name__startswith='hazard-motion-it').exists())

    def test_merging_keeps_every_rule_and_the_stronger_right(self):
        self.old_role('hazard-motion-it-pama', {'site': self.lmo, 'perusahaan': self.fad, 'bisa_ubah': False})
        self.old_role('hazard-motion-it-mtl', {'site': self.lmo, 'perusahaan': self.fad, 'bisa_ubah': True},
                      {'site': self.lmo, 'perusahaan': None, 'bisa_ubah': False})
        self.forwards()
        rules = {(r.site_id, r.perusahaan_id, r.bisa_ubah) for r in CakupanCctv.objects.filter(group__name='IT')}
        self.assertEqual(rules, {(self.lmo.pk, self.fad.pk, True), (self.lmo.pk, None, False)})

    def test_merging_keeps_permissions(self):
        permission = Permission.objects.first()
        self.old_role('hazard-motion-it-pama')
        self.old_role('hazard-motion-it-mtl').permissions.add(permission)
        self.forwards()
        self.assertIn(permission, Group.objects.get(name='IT').permissions.all())

    def test_a_custom_role_already_using_the_name_is_merged_not_lost(self):
        self.old_role('Admin').user_set.add(self.user)
        other = get_user_model().objects.create_user('ani', password='x')
        self.old_role('admin-hazard-motion').user_set.add(other)
        self.forwards()
        self.assertCountEqual(Group.objects.get(name='Admin').user_set.all(), [self.user, other])

    def test_running_twice_changes_nothing(self):
        self.old_role('survey-bc').user_set.add(self.user)
        self.forwards()
        self.forwards()
        self.assertEqual(list(self.user.groups.values_list('name', flat=True)), ['Survey BC'])

    def test_custom_coverage_roles_are_left_alone(self):
        self.old_role('CCTV LMO - PT Uji', {'site': self.lmo, 'perusahaan': self.fad}).user_set.add(self.user)
        self.forwards()
        self.assertEqual(list(self.user.groups.values_list('name', flat=True)), ['CCTV LMO - PT Uji'])

    def test_going_back_restores_the_old_names(self):
        self.forwards()
        Group.objects.get(name='Survey BC').user_set.add(self.user)
        migration.backwards(apps, None)
        self.assertEqual(list(self.user.groups.values_list('name', flat=True)), ['survey-bc'])
        self.assertTrue(Group.objects.filter(name='hazard-motion-it-mtl').exists())
        self.assertFalse(Group.objects.filter(name='IT').exists())
