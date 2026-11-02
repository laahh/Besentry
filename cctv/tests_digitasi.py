import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.gis.geos import Point, Polygon
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Perusahaan, Site
from navigation import roles
from navigation.menu import build_menu
from notifikasi.models import Notifikasi, PicSgi

from . import coverage
from .models import CakupanCctv, Cctv, CctvSpec, PengajuanCoverage

CAMERA = (117.60, 2.20)


def square(x, y, size=0.001):
    return json.dumps({'type': 'Polygon', 'coordinates': [[[x, y], [x + size, y], [x + size, y + size],
                                                           [x, y + size], [x, y]]]})


@override_settings(FONNTE_TOKEN='')
class DigitasiFlowTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.site = Site.objects.create(kode='BMO 1')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Berau Coal', kode='BC')
        it_role = Group.objects.create(name='CCTV BMO 1 - PT Berau Coal')
        CakupanCctv.objects.create(group=it_role, site=cls.site, perusahaan=cls.perusahaan, bisa_ubah=True)
        cls.it = User.objects.create_user('it', password='x')
        cls.it.groups.add(it_role)
        cls.sgi = User.objects.create_user('sgi', password='x', first_name='Fadil')
        cls.sgi.groups.add(Group.objects.get(name=roles.SGI))
        cls.safety = User.objects.create_user('safety', password='x')
        cls.safety.groups.add(Group.objects.get(name=roles.SAFETY))
        PicSgi.objects.create(nama='Fadil', email='fadil@example.com')

    def cctv_form(self, **extra):
        data = {'no_cctv': 'BMO1-BC-0001', 'nama': 'Pit Q1', 'site': self.site.pk,
                'perusahaan': self.perusahaan.pk, 'status': 'live', 'kondisi': 'baik',
                'longitude': str(CAMERA[0]), 'latitude': str(CAMERA[1]), 'lokasi_pemasangan': 'Bench 1',
                'spec-radius_m': '300'}
        data.update(extra)
        return data

    def it_adds_cctv(self):
        self.client.force_login(self.it)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cctv:create'), self.cctv_form())
        return PengajuanCoverage.objects.get()

    def test_new_cctv_creates_a_task_and_the_message_links_to_it(self):
        task = self.it_adds_cctv()
        self.assertEqual((task.status, task.jenis, task.versi, task.geom), ('menunggu_digitasi', 'baru', 1, None))
        self.assertIn(f'/spasial/digitasi/?id={task.pk}', Notifikasi.objects.get().pesan)

    def test_moving_a_cctv_creates_one_task_and_logs_further_moves(self):
        cctv = Cctv.objects.create(no_cctv='BMO1-BC-0001', nama='Pit Q1', site=self.site, perusahaan=self.perusahaan,
                                   posisi=Point(*CAMERA, srid=4326), lokasi_pemasangan='Bench 1')
        self.client.force_login(self.it)
        url = reverse('cctv:update', args=[cctv.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.cctv_form(nama='Nama baru saja'))  # not a move
        self.assertFalse(PengajuanCoverage.objects.exists())

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.cctv_form(lokasi_pemasangan='Bench 4'))
            self.client.post(url, self.cctv_form(lokasi_pemasangan='Bench 5'))
        task = PengajuanCoverage.objects.get()
        self.assertEqual(task.jenis, 'dipindah')
        notes = list(task.riwayat.values_list('catatan', flat=True))
        self.assertIn('Lokasi pemasangan: Bench 1 → Bench 4', notes[0])
        self.assertIn('perubahan lagi', notes[1])

    def test_sgi_draws_saves_a_draft_then_sends_to_safety(self):
        task = self.it_adds_cctv()
        self.client.force_login(self.sgi)
        page = self.client.get(reverse('cctv:digitasi_index'))
        self.assertEqual(page.context['p'], task)

        simpan = reverse('cctv:digitasi_simpan', args=[task.pk])
        self.client.post(simpan, {'aksi': 'draft', 'geom_json': square(*CAMERA)})
        task.refresh_from_db()
        self.assertEqual(task.status, 'menunggu_digitasi')
        self.assertIsNotNone(task.geom)

        far = square(CAMERA[0] + 0.02, CAMERA[1])  # ~2 km away, radius 300 m
        response = self.client.post(simpan, {'aksi': 'kirim', 'geom_json': far}, follow=True)
        self.assertContains(response, 'melebihi batas')
        task.refresh_from_db()
        self.assertEqual(task.status, 'menunggu_digitasi')

        self.client.post(simpan, {'aksi': 'kirim', 'geom_json': square(*CAMERA), 'catatan': 'Bench timur dikeluarkan'})
        task.refresh_from_db()
        self.assertEqual((task.status, task.digitasi_oleh), ('menunggu_safety', self.sgi))
        self.assertIn('Bench timur dikeluarkan', task.riwayat.get(aksi='digitasi').catatan)

    def test_safety_approves_or_sends_it_back_to_sgi(self):
        task = self.it_adds_cctv()
        coverage.save_digitasi(task, self.sgi, coverage.parse_geometry(square(*CAMERA)), kirim=True)

        self.client.force_login(self.safety)
        review = reverse('cctv:coverage_review', args=[task.pk])
        self.client.post(review, {'keputusan': 'kembalikan', 'catatan': 'Timbunan belum dikeluarkan'})
        task.refresh_from_db()
        self.assertEqual((task.status, task.digitasi_pada), ('menunggu_digitasi', None))

        coverage.save_digitasi(task, self.sgi, coverage.parse_geometry(square(*CAMERA, 0.0008)), kirim=True)
        self.client.post(review, {'keputusan': 'setujui',
                                  'checklist': [k for k, _ in PengajuanCoverage.CHECKLIST_SAFETY]})
        task.refresh_from_db()
        self.assertEqual(task.status, 'selesai')
        active, versi = coverage.active_coverage(task.cctv)
        self.assertTrue(active.equals(task.geom))

    def test_roles(self):
        task = self.it_adds_cctv()
        # IT cannot digitise; Safety cannot digitise either.
        for user in (self.it, self.safety):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('cctv:digitasi_index')).status_code, 403)
        # SGI cannot approve the Safety stage.
        coverage.save_digitasi(task, self.sgi, coverage.parse_geometry(square(*CAMERA)), kirim=True)
        self.client.force_login(self.sgi)
        response = self.client.post(reverse('cctv:coverage_review', args=[task.pk]), {'keputusan': 'setujui'})
        self.assertEqual(response.status_code, 403)
        # SGI and Safety see requests from every site in Verifikasi Coverage.
        for user in (self.sgi, self.safety):
            self.client.force_login(user)
            page = self.client.get(reverse('cctv:coverage_index'), {'status': 'menunggu_safety'})
            self.assertEqual([p.pk for p in page.context['queue']], [task.pk])

    def test_menu(self):
        def titles(user):
            return [c['title'] for s in build_menu(user) for i in s['items'] for c in [i, *i['children']]]

        sgi_titles = titles(get_user_model().objects.get(pk=self.sgi.pk))
        self.assertIn('Digitasi Coverage', sgi_titles)
        self.assertIn('Verifikasi Coverage', sgi_titles)
        self.assertNotIn('Orthophoto', sgi_titles)
        it_titles = titles(get_user_model().objects.get(pk=self.it.pk))
        self.assertNotIn('Digitasi Coverage', it_titles)
        self.assertIn('Verifikasi Coverage', titles(get_user_model().objects.get(pk=self.safety.pk)))
