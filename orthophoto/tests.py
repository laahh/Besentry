import datetime
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus

from navigation import roles

from . import jobs, services
from .forms import parse_bulan
from .models import Orthophoto

TILEJSON = {'bounds': [117.27, 2.03, 117.30, 2.05], 'minzoom': 14, 'maxzoom': 18}


def fake_convert(src, dst, on_progress):
    on_progress(50)
    Path(dst).write_bytes(b'cog')


class TempDirsMixin:
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.incoming = Path(tmp.name) / 'incoming'
        self.cog = Path(tmp.name) / 'cog'
        self.incoming.mkdir()
        self.cog.mkdir()
        override = override_settings(ORTHOPHOTO_INCOMING_DIR=self.incoming, ORTHOPHOTO_COG_DIR=self.cog)
        override.enable()
        self.addCleanup(override.disable)

    def raw(self, name='raw.tif'):
        (self.incoming / name).write_bytes(b'raw')
        return name


class ProgressParserTests(TestCase):
    def test_reads_gdal_progress(self):
        parser = services.ProgressParser()
        seen = [p for c in 'Input file size is 6000, 4000\n0...10...20...30.ERROR 1: x 2.5\n100 - done.'
                if (p := parser.feed(c)) is not None]
        self.assertEqual(seen, [0, 10, 20, 30])


class ParseBulanTests(TestCase):
    def test_month(self):
        self.assertEqual(parse_bulan('2026-11'), datetime.date(2026, 11, 1))

    def test_rejects_other_formats(self):
        for value in ('2026-13', '11-2026', '', None):
            with self.assertRaises(ValueError):
                parse_bulan(value)


@mock.patch.object(services, 'read_tilejson', return_value=TILEJSON)
@mock.patch.object(services, 'convert_to_cog', side_effect=fake_convert)
class PublishTests(TempDirsMixin, TestCase):
    def _publish(self, bulan='2026-11'):
        ortho = Orthophoto.objects.create(bulan=parse_bulan(bulan), file_sumber=self.raw())
        services.publish(ortho.pk)
        ortho.refresh_from_db()
        return ortho

    def test_first_publish_becomes_current(self, convert, tilejson):
        ortho = self._publish()
        self.assertEqual(ortho.status, Orthophoto.Status.SIAP)
        self.assertTrue(ortho.aktif)
        self.assertEqual(ortho.file_cog, f'ortho_2026-11_{ortho.pk}.tif')
        self.assertEqual(ortho.batas.extent, tuple(TILEJSON['bounds']))
        self.assertEqual((ortho.zoom_min, ortho.zoom_maks), (14, 18))
        self.assertEqual([p.name for p in self.cog.iterdir()], [ortho.file_cog])

    def test_next_publish_replaces_and_deletes_previous(self, convert, tilejson):
        old = self._publish('2026-10')
        new = self._publish('2026-11')
        old.refresh_from_db()
        self.assertEqual(old.status, Orthophoto.Status.DIGANTI)
        self.assertFalse(old.aktif)
        self.assertTrue(new.aktif)
        self.assertEqual([p.name for p in self.cog.iterdir()], [new.file_cog])

    def test_failure_keeps_current_month(self, convert, tilejson):
        old = self._publish('2026-10')
        tilejson.side_effect = services.PublishError('TiTiler tidak bisa membaca COG (500)')
        ortho = Orthophoto.objects.create(bulan=parse_bulan('2026-11'), file_sumber=self.raw())

        with self.assertRaises(services.PublishError):
            services.publish(ortho.pk)

        ortho.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(ortho.status, Orthophoto.Status.GAGAL)
        self.assertIn('500', ortho.pesan_error)
        self.assertTrue(old.aktif)
        self.assertEqual([p.name for p in self.cog.iterdir()], [old.file_cog])

    def test_missing_source_file_fails(self, convert, tilejson):
        ortho = Orthophoto.objects.create(bulan=parse_bulan('2026-11'), file_sumber='hilang.tif')
        with self.assertRaises(services.PublishError):
            services.publish(ortho.pk)
        ortho.refresh_from_db()
        self.assertEqual(ortho.status, Orthophoto.Status.GAGAL)
        convert.assert_not_called()


class ConvertTests(TempDirsMixin, TestCase):
    def test_gdal_errors_fail_even_with_exit_code_zero(self):
        output = '0...10.ERROR 1: JPEGLib:Too many color components\n100 - done.'
        process = mock.Mock(stdout=mock.Mock(read=mock.Mock(side_effect=[*output, ''])))
        process.wait.return_value = 0
        process.poll.return_value = 0
        with mock.patch.object(services.subprocess, 'Popen', return_value=process):
            with self.assertRaisesMessage(services.PublishError, 'Too many color components'):
                services.convert_to_cog('a.tif', 'b.tif', lambda percent: None)


@mock.patch('orthophoto.jobs.django_rq.get_connection')
class SyncWithQueueTests(TestCase):
    def _ortho(self, minutes_old=5, **fields):
        ortho = Orthophoto.objects.create(bulan=parse_bulan('2026-09'), file_sumber='a.tif', **fields)
        Orthophoto.objects.filter(pk=ortho.pk).update(
            dibuat=timezone.now() - datetime.timedelta(minutes=minutes_old))
        return ortho

    def test_failed_job_marks_record_failed_with_its_error(self, connection):
        ortho = self._ortho(job_id='orthophoto-1')
        job = mock.Mock()
        job.get_status.return_value = JobStatus.FAILED
        job.latest_result.return_value.exc_string = (
            'Traceback (most recent call last):\n  ...\nPermissionError: [Errno 13] /app/data/cog')
        with mock.patch.object(jobs.Job, 'fetch', return_value=job):
            self.assertEqual(jobs.sync_with_queue(), 1)
        ortho.refresh_from_db()
        self.assertEqual(ortho.status, Orthophoto.Status.GAGAL)
        self.assertEqual(ortho.pesan_error, 'PermissionError: [Errno 13] /app/data/cog')

    def test_queued_job_is_left_alone(self, connection):
        ortho = self._ortho(job_id='orthophoto-1')
        job = mock.Mock()
        job.get_status.return_value = JobStatus.QUEUED
        with mock.patch.object(jobs.Job, 'fetch', return_value=job):
            self.assertEqual(jobs.sync_with_queue(), 0)
        ortho.refresh_from_db()
        self.assertEqual(ortho.status, Orthophoto.Status.MENUNGGU)

    def test_missing_job_fails_only_after_grace_period(self, connection):
        fresh = self._ortho(minutes_old=0)
        old = self._ortho(job_id='orthophoto-x')
        with mock.patch.object(jobs.Job, 'fetch', side_effect=NoSuchJobError):
            jobs.sync_with_queue()
        fresh.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(fresh.status, Orthophoto.Status.MENUNGGU)
        self.assertEqual((old.status, old.pesan_error), (Orthophoto.Status.GAGAL, jobs.MISSING_JOB_MESSAGE))


class ViewTests(TempDirsMixin, TestCase):
    def setUp(self):
        super().setUp()
        sync = mock.patch('orthophoto.jobs.sync_with_queue', return_value=0)
        sync.start()
        self.addCleanup(sync.stop)
        User = get_user_model()
        self.admin = User.objects.create_user('admin', password='x')
        self.admin.groups.add(Group.objects.get(name=roles.ADMIN_HAZARD_MOTION))
        self.other = User.objects.create_user('other', password='x')

    def test_publish_page_is_admin_only(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('orthophoto:publish')).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('orthophoto:publish'))
        self.assertContains(response, 'Survey &amp; Geospatial Information')

    @mock.patch('orthophoto.jobs.enqueue_publish')
    def test_publish_creates_record_and_enqueues(self, enqueue):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('orthophoto:publish'),
                                        {'file_sumber': self.raw(), 'bulan': '2026-11'})
        self.assertRedirects(response, reverse('orthophoto:publish'))
        ortho = Orthophoto.objects.get()
        self.assertEqual((ortho.bulan, ortho.status, ortho.dibuat_oleh),
                         (datetime.date(2026, 11, 1), Orthophoto.Status.MENUNGGU, self.admin))
        enqueue.assert_called_once_with(ortho)

    @mock.patch('orthophoto.jobs.enqueue_publish')
    def test_only_files_in_incoming_can_be_picked(self, enqueue):
        self.client.force_login(self.admin)
        self.raw()
        response = self.client.post(reverse('orthophoto:publish'),
                                    {'file_sumber': '../../.env', 'bulan': '2026-11'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Orthophoto.objects.exists())
        enqueue.assert_not_called()

    @mock.patch('orthophoto.jobs.enqueue_publish')
    def test_one_publish_at_a_time(self, enqueue):
        Orthophoto.objects.create(bulan=parse_bulan('2026-10'), file_sumber='a.tif',
                                  status=Orthophoto.Status.DIPROSES)
        self.client.force_login(self.admin)
        self.client.post(reverse('orthophoto:publish'), {'file_sumber': self.raw(), 'bulan': '2026-11'})
        self.assertEqual(Orthophoto.objects.count(), 1)
        enqueue.assert_not_called()

    def test_current_api(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('orthophoto:current')).status_code, 404)

        ortho = Orthophoto.objects.create(bulan=parse_bulan('2026-11'), file_sumber='a.tif',
                                          status=Orthophoto.Status.SIAP, aktif=True, zoom_min=14, zoom_maks=18)
        Orthophoto.objects.filter(pk=ortho.pk).update(file_cog='ortho_2026-11_1.tif')
        ortho.refresh_from_db()
        services._make_current(ortho, TILEJSON)

        data = self.client.get(reverse('orthophoto:current')).json()
        self.assertEqual(data['bulan'], '2026-11')
        self.assertEqual(data['bounds'], TILEJSON['bounds'])
        self.assertIn('/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.webp?url=%2Fdata%2Fcog%2Fortho_2026-11_1.tif',
                      data['tile_url'])
