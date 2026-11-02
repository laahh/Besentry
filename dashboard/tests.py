from django.contrib.auth import get_user_model
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import TestCase
from django.urls import reverse

from core.models import Zona

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
