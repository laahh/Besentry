import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from . import roles
from .menu import MAIN_MENU, MITRA_MENU, build_menu
from .pages import PAGES

FEATHER_JS = settings.BASE_DIR / 'static/assets/js/icons/feather-icon/feather.min.js'


def _walk(sections):
    for section in sections:
        for item in section.items:
            yield item
            yield from item.children


def _titles(menu):
    return [section['title'] for section in menu]


def _all_titles(menu):
    titles = []
    for section in menu:
        for item in section['items']:
            titles.append(item['title'])
            titles.extend(child['title'] for child in item['children'])
    return titles


def _urls_in_dom_order(menu):
    urls = []
    for section in menu:
        for item in section['items']:
            urls.append(item['url'])
            urls.extend(child['url'] for child in item['children'])
    return [url for url in urls if url.startswith('/')]


class MenuDefinitionTests(TestCase):
    def test_every_page_is_reachable_from_a_menu(self):
        used = {item.page for item in _walk(MAIN_MENU + MITRA_MENU) if item.page}
        self.assertEqual(used, {page.name for page in PAGES})

    def test_every_icon_exists_in_bundled_feather(self):
        # An unknown icon stops feather from drawing every icon after it on the page.
        # Icons are `name: '<svg...'` entries; a bare `name:` also matches plain JS such as `key:`.
        bundle = FEATHER_JS.read_text(encoding='utf-8')
        available = set(re.findall(r'''(?<![\w-])["']?([a-z][a-z0-9-]*)["']?:\s*['"]<''', bundle))
        used = {item.icon for item in _walk(MAIN_MENU + MITRA_MENU) if item.icon}
        for template in (settings.BASE_DIR / 'templates').rglob('*.html'):
            used |= set(re.findall(r'data-feather="([a-z0-9-]+)"', template.read_text(encoding='utf-8')))
        self.assertEqual(sorted(used - available), [])

    def test_sidebar_active_link_matches_itself(self):
        # sidebar-menu.js marks the first link whose href is contained in the pathname.
        superuser = get_user_model().objects.create_superuser('root', password='x')
        urls = _urls_in_dom_order(build_menu(superuser))
        for url in urls:
            first_match = next(candidate for candidate in urls if candidate in url)
            self.assertEqual(first_match, url, f'{url} would highlight {first_match}')


class MenuVisibilityTests(TestCase):
    def setUp(self):
        self.User = get_user_model()

    def _user(self, *groups, **extra):
        user = self.User.objects.create_user(f'u{self.User.objects.count()}', password='x', **extra)
        user.groups.set(Group.objects.filter(name__in=groups))
        return user

    def test_role_groups_are_created_by_migration(self):
        self.assertEqual(set(Group.objects.values_list('name', flat=True)) & set(roles.ALL_ROLES),
                         set(roles.ALL_ROLES))

    def test_superuser_sees_every_section(self):
        menu = build_menu(self.User.objects.create_superuser('root', password='x'))
        self.assertEqual(_titles(menu), [section.title for section in MAIN_MENU])

    def test_regular_user_hides_admin_only_entries(self):
        titles = _all_titles(build_menu(self._user()))
        self.assertIn('Pengawas Control Room', titles)
        self.assertIn('Dashboard Readiness', titles)
        for hidden in ('CCTV Database', 'Spasial', 'WMS', 'Zona', 'Admin'):
            self.assertNotIn(hidden, titles)
        self.assertNotIn('Umum', _titles(build_menu(self._user())))

    def test_it_pama_sees_cctv_admin_pages_but_not_spasial(self):
        titles = _all_titles(build_menu(self._user(roles.IT_PAMA)))
        self.assertIn('CCTV Database', titles)
        self.assertNotIn('Spasial', titles)

    def test_admin_hazard_motion_sees_dashboard_and_spasial(self):
        titles = _all_titles(build_menu(self._user(roles.ADMIN_HAZARD_MOTION)))
        for visible in ('Dashboard', 'Spasial', 'Boundary Monitoring', 'CCTV Coverage'):
            self.assertIn(visible, titles)

    def test_mitra_only_user_sees_only_mitra_menu(self):
        menu = build_menu(self._user(roles.MITRA_ONLY))
        self.assertEqual(_all_titles(menu), ['Mitra Kerja', 'Evaluasi PVT'])

    def test_anonymous_user_gets_no_menu(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(build_menu(AnonymousUser()), [])


class PlaceholderPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('budi', password='x')
        self.client.force_login(self.user)

    def test_open_page_renders_with_legacy_route(self):
        response = self.client.get(reverse('navigation:page', kwargs={'name': 'dashboard-readiness'}))
        self.assertEqual(response.request['PATH_INFO'], '/maps/')
        self.assertContains(response, 'Dashboard Readiness')
        self.assertContains(response, "route('maps.map')")

    def test_sidebar_is_rendered_from_menu(self):
        response = self.client.get(reverse('navigation:page', kwargs={'name': 'history-p2h'}))
        self.assertContains(response, '<h6>Control Room</h6>', html=True)
        self.assertContains(response, 'href="/smart-alert-maps/"')

    def test_role_restricted_page_is_forbidden(self):
        response = self.client.get(reverse('navigation:page', kwargs={'name': 'spasial-wms'}))
        self.assertEqual(response.status_code, 403)

    def test_mitra_only_user_cannot_open_other_pages(self):
        self.user.groups.add(Group.objects.get(name=roles.MITRA_ONLY))
        allowed = self.client.get(reverse('navigation:page', kwargs={'name': 'bewell-pvt'}))
        blocked = self.client.get(reverse('navigation:page', kwargs={'name': 'dop'}))
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(blocked.status_code, 403)

    def test_pages_require_login(self):
        self.client.logout()
        response = self.client.get('/maps/')
        self.assertRedirects(response, f"{reverse('login')}?next=/maps/")
