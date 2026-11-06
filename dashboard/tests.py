from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import TestCase
from django.urls import reverse

from core.models import Zona
from navigation import roles

# ~0.01 deg square near Tanjung Redeb, Berau (roughly 1.1 km x 1.1 km)
SAMPLE_POLYGON = Polygon.from_bbox((117.49, 2.15, 117.50, 2.16))


class DashboardAccessTests(TestCase):
    def test_root_redirects_to_dashboard(self):
        response = self.client.get('/')
        self.assertRedirects(response, reverse('dashboard:index'), fetch_redirect_response=False)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard:index'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard:index')}")

    def test_login_page_renders_viho_form(self):
        response = self.client.get(reverse('login'))
        self.assertContains(response, 'login-card')
        self.assertContains(response, 'name="username"')

    def test_wrong_password_shows_error(self):
        get_user_model().objects.create_user('budi', password='rahasia-123')
        response = self.client.post(reverse('login'), {'username': 'budi', 'password': 'salah'})
        self.assertContains(response, 'SID/username atau password salah')


class DashboardLandingTests(TestCase):
    def open_as(self, *role_names):
        user = get_user_model().objects.create_user('budi', password='rahasia-123')
        user.groups.add(*[Group.objects.get_or_create(name=name)[0] for name in role_names])
        self.client.force_login(user)
        return self.client.get(reverse('dashboard:index'))

    def test_who_may_see_coverage_lands_on_dashboard_coverage(self):
        for role in (roles.ADMIN, roles.SURVEY_BC, roles.MINING, roles.SAFETY):
            with self.subTest(role):
                get_user_model().objects.filter(username='budi').delete()
                self.assertRedirects(self.open_as(role), reverse('coverage:index'), fetch_redirect_response=False)

    def test_redirect_opens_the_coverage_page(self):
        response = self.open_as(roles.ADMIN)
        self.assertEqual(self.client.get(response.url).status_code, 200)

    def test_contractor_stays_on_the_old_page_instead_of_being_refused(self):
        response = self.open_as(roles.SURVEY_MITRA)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/index.html')

    def test_login_ends_on_dashboard_coverage(self):
        user = get_user_model().objects.create_user('admin', password='rahasia-123')
        user.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        response = self.client.post(reverse('login'), {'username': 'admin', 'password': 'rahasia-123'}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], reverse('coverage:index'))
        self.assertEqual(response.status_code, 200)


class DashboardContentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'budi', password='rahasia-123', first_name='Budi', is_staff=True,
        )
        self.client.force_login(self.user)

    def test_empty_state_when_no_zona(self):
        response = self.client.get(reverse('dashboard:index'))
        self.assertContains(response, 'Selamat datang, Budi!')
        self.assertContains(response, 'Belum ada zona.')
        self.assertEqual(response.context['total_luas_ha'], 0)

    def test_zona_stats_and_area_in_hectares(self):
        Zona.objects.create(nama='Pit A', geom=MultiPolygon(SAMPLE_POLYGON, srid=4326))

        response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.context['total_zona'], 1)
        self.assertAlmostEqual(response.context['total_luas_ha'], 123.6, delta=2)
        self.assertContains(response, 'Pit A')

    def test_map_loads_maplibre_and_current_orthophoto(self):
        response = self.client.get(reverse('dashboard:index'))
        self.assertContains(response, '"maplibre-gl": "https://unpkg.com/maplibre-gl@')
        self.assertContains(response, 'id="peta"')
        self.assertContains(response, reverse('orthophoto:current'))

    def test_logout_via_post_returns_to_login(self):
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
