from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone as django_timezone

from core.models import Perusahaan, Site
from navigation import roles

from .models import CakupanCctv, Cctv, PengajuanCoverage, RiwayatPengajuan
from .sla import format_durasi, stage_summary, stage_times

URL = '/cctv/tasklist/'
START = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
Aksi = RiwayatPengajuan.Aksi


def at(hours):
    return START + timedelta(hours=hours)


def entry(aksi, hours, catatan=''):
    return RiwayatPengajuan(aksi=aksi, waktu=at(hours), catatan=catatan)


def request(status, kode='PM'):
    perusahaan = Perusahaan(nama='PT', kode=kode)
    cctv = Cctv(pk=1, no_cctv='X-1', perusahaan=perusahaan)
    return PengajuanCoverage(cctv=cctv, jenis='dipindah', status=status, dibuat=START)


class FormatDurasiTests(SimpleTestCase):
    def test_shows_the_two_largest_units(self):
        cases = (
            (timedelta(seconds=20), '< 1 menit'),
            (timedelta(minutes=12), '12 menit'),
            (timedelta(hours=2, minutes=5), '2 jam 5 menit'),
            (timedelta(hours=3), '3 jam'),
            (timedelta(days=3, hours=11, minutes=30), '3 hari 11 jam'),
            (timedelta(days=2), '2 hari'),
        )
        for value, expected in cases:
            with self.subTest(expected):
                self.assertEqual(format_durasi(value), expected)


class StageTimesTests(SimpleTestCase):
    def test_each_event_closes_its_stage_and_the_current_stage_keeps_running(self):
        entries = [entry(Aksi.DIAJUKAN, 2), entry(Aksi.DIGITASI, 5), entry(Aksi.VALIDASI, 5)]
        spent = stage_times(request('menunggu_sgi'), entries, at(9))
        self.assertEqual(spent, {'it': timedelta(hours=2), 'mitra': timedelta(hours=3), 'sgi': timedelta(hours=4)})

    def test_time_after_a_return_adds_to_the_stage_it_went_back_to(self):
        entries = [
            entry(Aksi.DIAJUKAN, 1),
            entry(Aksi.DIGITASI, 3),
            entry(Aksi.DIKEMBALIKAN, 4, 'Survey BC mengembalikan ke Survey Mitra: geser'),
            entry(Aksi.DIGITASI, 9),
            entry(Aksi.DISETUJUI_SGI, 10),
        ]
        spent = stage_times(request('menunggu_safety'), entries, at(12))
        self.assertEqual(spent['mitra'], timedelta(hours=7))
        self.assertEqual(spent['sgi'], timedelta(hours=2))
        self.assertEqual(spent['safety'], timedelta(hours=2))

    def test_safety_returning_is_closed_as_safety(self):
        entries = [
            entry(Aksi.DIAJUKAN, 1),
            entry(Aksi.DIGITASI, 2),
            entry(Aksi.DISETUJUI_SGI, 3),
            entry(Aksi.DIKEMBALIKAN, 8, 'Safety BC mengembalikan ke SGI: kamera'),
        ]
        spent = stage_times(request('return_sgi'), entries, at(10))
        self.assertEqual(spent['safety'], timedelta(hours=5))
        self.assertEqual(spent['sgi'], timedelta(hours=3))

    def test_finished_request_stops_counting(self):
        entries = [entry(Aksi.DIAJUKAN, 1), entry(Aksi.DIGITASI, 2), entry(Aksi.DISETUJUI_SGI, 3),
                   entry(Aksi.DISETUJUI_SAFETY, 5)]
        spent = stage_times(request('approved'), entries, at(100))
        self.assertEqual(spent['safety'], timedelta(hours=2))
        self.assertNotIn('selesai', spent)

    def test_berau_coal_digitising_counts_as_survey_bc(self):
        entries = [entry(Aksi.DIAJUKAN, 1), entry(Aksi.DIGITASI, 6), entry(Aksi.VALIDASI, 6),
                   entry(Aksi.DISETUJUI_SGI, 6)]
        spent = stage_times(request('menunggu_safety', kode=Perusahaan.KODE_BERAU_COAL), entries, at(8))
        self.assertNotIn('mitra', spent)
        self.assertEqual(spent['sgi'], timedelta(hours=5))

    def test_old_history_without_survey_bc_approval_is_read_from_the_current_status(self):
        entries = [entry(Aksi.DIAJUKAN, 1), entry(Aksi.DIGITASI, 2)]
        spent = stage_times(request('menunggu_safety'), entries, at(5))
        self.assertEqual(spent['safety'], timedelta(hours=3))
        self.assertNotIn('sgi', spent)

    def test_history_older_than_the_request_never_goes_negative(self):
        spent = stage_times(request('menunggu_mitra'), [entry(Aksi.DIAJUKAN, -5)], at(1))
        self.assertEqual(spent['it'], timedelta(0))


class StageSummaryTests(SimpleTestCase):
    def test_open_and_finished_times_per_stage(self):
        items = [
            (request('menunggu_sgi'), {'it': timedelta(hours=2), 'sgi': timedelta(hours=4)}),
            (request('menunggu_sgi'), {'it': timedelta(hours=4), 'sgi': timedelta(hours=8)}),
            (request('return_mitra'), {'it': timedelta(hours=6), 'mitra': timedelta(hours=1)}),
        ]
        rows = {row['key']: row for row in stage_summary(items, (('it', 'IT'), ('mitra', 'Mitra'), ('sgi', 'SGI')))}
        self.assertEqual(rows['sgi']['open'], 2)
        self.assertEqual(rows['sgi']['rata_tunggu'], '6 jam')
        self.assertEqual(rows['sgi']['terlama'], '8 jam')
        self.assertEqual(rows['sgi']['rata_selesai'], '')
        self.assertEqual(rows['mitra']['dikembalikan'], 1)
        self.assertEqual(rows['it']['open'], 0)
        self.assertEqual(rows['it']['rata_tunggu'], '')
        self.assertEqual(rows['it']['rata_selesai'], '4 jam')


class TasklistStatistikTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.lmo, cls.smo = Site.objects.create(kode='LMO'), Site.objects.create(kode='SMO')
        cls.perusahaan = Perusahaan.objects.create(nama='PT Fajar', kode='FAD')
        cls.admin = get_user_model().objects.create_user('admin', password='x')
        cls.admin.groups.add(Group.objects.get_or_create(name=roles.ADMIN)[0])
        cls.number = 0

    def request(self, status, site=None, **extra):
        TasklistStatistikTests.number += 1
        cctv = Cctv.objects.create(no_cctv=f'S-{self.number}', nama='Kamera', site=site or self.lmo,
                                   perusahaan=self.perusahaan)
        return PengajuanCoverage.objects.create(cctv=cctv, jenis='dipindah', status=status, **extra)

    def ago(self, pengajuan, aksi, **delta):
        moment = django_timezone.now() - timedelta(**delta)
        item = RiwayatPengajuan.objects.create(pengajuan=pengajuan, aksi=aksi)
        RiwayatPengajuan.objects.filter(pk=item.pk).update(waktu=moment)

    def open_page(self, user=None, **query):
        self.client.force_login(user or self.admin)
        return self.client.get(URL, query)

    def test_cards_count_every_request_the_user_may_open(self):
        waiting = self.request('menunggu_sgi')
        self.request('return_mitra')
        self.request('approved')
        self.request('approved_warning')
        self.ago(waiting, Aksi.DIGITASI, days=2, hours=3)
        stats = self.open_page().context['statistik']
        self.assertEqual(stats['total'], 4)
        self.assertEqual(stats['open'], 2)
        self.assertEqual(stats['dikembalikan'], 1)
        self.assertEqual(stats['selesai'], 2)
        self.assertEqual(stats['selesai_warning'], 1)
        self.assertEqual(stats['terlama'], '2 hari 3 jam')
        self.assertEqual(stats['terlama_nama'], waiting.no_cctv)

    def test_cards_stay_the_same_when_the_admin_switches_view(self):
        self.request('menunggu_sgi')
        everyone = self.open_page().context['statistik']
        mine = self.open_page(lihat='saya').context['statistik']
        self.assertEqual(everyone, mine)
        self.assertEqual(everyone['tugas_saya'], 0)

    def test_cards_respect_the_cctv_scope_and_private_drafts(self):
        role = Group.objects.create(name='CCTV LMO')
        CakupanCctv.objects.create(group=role, site=self.lmo, perusahaan=self.perusahaan)
        mitra = get_user_model().objects.create_user('mitra', password='x')
        mitra.groups.add(Group.objects.get_or_create(name=roles.SURVEY_MITRA)[0], role)
        self.request('menunggu_mitra')
        self.request('menunggu_mitra', site=self.smo)
        self.request('draft_it')
        stats = self.open_page(mitra).context['statistik']
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['tugas_saya'], 1)

    def test_empty_tasklist_has_zero_cards(self):
        stats = self.open_page().context['statistik']
        self.assertEqual((stats['total'], stats['open']), (0, 0))
        self.assertEqual(stats['terlama'], '')

    def test_task_row_and_progress_show_the_time_in_each_stage(self):
        pengajuan = self.request('menunggu_sgi')
        self.ago(pengajuan, Aksi.DIAJUKAN, days=3)
        self.ago(pengajuan, Aksi.DIGITASI, days=1)
        page = self.open_page()
        self.assertEqual(page.context['rows'][0]['di_tahap'], '1 hari')
        steps = {step['label']: step for step in page.context['steps']}
        self.assertEqual(steps['Digitasi Survey Mitra']['durasi'], '2 hari')
        self.assertEqual(steps['Review Survey BC']['durasi'], '1 hari')
        self.assertEqual(steps['Review Safety BC']['durasi'], '')
        self.assertContains(page, 'Di tahap ini 1 hari')
        self.assertContains(page, 'Durasi: 2 hari')
        self.assertContains(page, 'Berjalan: 1 hari')

    def test_stage_table_is_shown_with_its_times(self):
        pengajuan = self.request('menunggu_safety')
        self.ago(pengajuan, Aksi.DISETUJUI_SGI, hours=5)
        page = self.open_page()
        safety = next(row for row in page.context['sla_tahap'] if row['key'] == 'safety')
        self.assertEqual(safety['open'], 1)
        self.assertEqual(safety['terlama'], '5 jam')
        self.assertContains(page, 'SLA per tahap')
        self.assertEqual([row['key'] for row in page.context['sla_tahap']], ['it', 'mitra', 'sgi', 'safety'])

    def test_group_header_shows_its_longest_wait(self):
        first = self.request('menunggu_sgi')
        second = self.request('menunggu_sgi')
        self.ago(first, Aksi.DIGITASI, hours=2)
        self.ago(second, Aksi.DIGITASI, hours=7)
        group = self.open_page().context['groups'][0]
        self.assertEqual(group['terlama'], '7 jam')

    def test_query_count_does_not_grow_with_the_requests(self):
        self.request('menunggu_sgi')
        self.client.force_login(self.admin)
        self.client.get(URL)
        with CaptureQueriesContext(connection) as first:
            self.client.get(URL)
        for _ in range(5):
            self.request('menunggu_sgi')
        with self.assertNumQueries(len(first.captured_queries)):
            self.client.get(URL)
