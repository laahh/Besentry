import csv
import datetime
import json
import shutil
import subprocess
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase

from areakerja.models import AreaKerja, MitraSite, PengajuanAreaKerja
from cctv.models import Cctv, CctvCoverage
from cctv.tests_pengajuan import CAMERA, square
from core.models import Perusahaan, Site

X, Y = CAMERA
W45 = datetime.date(2026, 11, 2)


def shapefile(path, features):
    """features: [(GEOS geometry in WGS 84, {field: value})] -> a shapefile in UTM 50N at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    collection = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': props, 'geometry': json.loads(geom.geojson)} for geom, props in features]}
    source = path.with_suffix('.geojson')
    source.write_text(json.dumps(collection))
    subprocess.run(['ogr2ogr', '-t_srs', 'EPSG:32650', '-f', 'ESRI Shapefile', str(path), str(source)],
                   check=True, capture_output=True)
    source.unlink()


class ImportGeometriTests(TestCase):
    databases = {'default', 'log'}

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(kode='LMO')
        cls.pama = Perusahaan.objects.create(nama='PT Pamapersada Nusantara', kode='PM')
        cls.cctv = Cctv.objects.create(no_cctv='LMO-PM-0014', nama='Pit J', site=cls.site, perusahaan=cls.pama,
                                       jenis='fixed', posisi=Point(X, Y, srid=4326))
        cls.named = Cctv.objects.create(no_cctv='LMO-PM-0020', nama='CS - Parkiran', site=cls.site, perusahaan=cls.pama,
                                        jenis='fixed', posisi=Point(X, Y, srid=4326))

    def setUp(self):
        transaction.get_connection().run_on_commit.clear()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        folder = self.root / 'LMO_PAMA_2026-W45'
        moved = Point(X + 0.0005, Y + 0.0005, srid=4326)
        shapefile(folder / 'point' / 'cctv_point.shp', [
            (moved, {'No_CCTV': 'LMO-PM-0014\n', 'Nama_CCTV': ''}),           # line break in the number
            (Point(X + 0.001, Y, srid=4326), {'No_CCTV': '', 'Nama_CCTV': 'CS - Parkiran'}),  # by name
            (Point(X, Y, srid=4326), {'No_CCTV': 'LMO-PM-9999', 'Nama_CCTV': ''}),  # not in the DB
        ])
        shapefile(folder / 'coverage' / 'coverage.shp', [
            (square(X, Y, 0.001), {'no_cctv': 'LMO-PM-0014', 'nama_cctv': ''}),
            (square(X + 0.001, Y, 0.001), {'no_cctv': 'LMO-PM-0014', 'nama_cctv': ''}),  # merged with the first
            (square(X, Y + 0.002, 0.001), {'no_cctv': '', 'nama_cctv': 'CS - Parkiran'}),  # no number: skipped
        ])
        shapefile(folder / 'working_area' / 'working_area.shp', [
            (square(X, Y, 0.004), {'area_kerja': 'PIT'}),
            (square(X + 0.002, Y, 0.004), {'area_kerja': 'HAULING'}),  # half under the pit: clipped
        ])
        self.moved = moved

    def run_import(self):
        report = self.root / 'laporan.csv'
        with self.captureOnCommitCallbacks(execute=True):
            call_command('import_geometri', str(self.root), laporan=str(report), stdout=StringIO())
        return list(csv.DictReader(open(report, encoding='utf-8-sig')))

    def test_positions_coverage_and_work_areas(self):
        rows = self.run_import()
        self.cctv.refresh_from_db()
        self.named.refresh_from_db()
        self.assertAlmostEqual(self.cctv.posisi.x, self.moved.x, places=6)
        self.assertAlmostEqual(self.named.posisi.x, X + 0.001, places=6)
        coverage = CctvCoverage.objects.get(cctv=self.cctv)
        self.assertEqual(coverage.minggu, W45)
        self.assertAlmostEqual(coverage.geom.transform(32650, clone=True).area / 10_000, 2.4, delta=0.1)  # two squares
        self.assertFalse(CctvCoverage.objects.filter(cctv=self.named).exists())

        pengajuan = PengajuanAreaKerja.objects.get(mitra_site__site=self.site, mitra_site__perusahaan=self.pama)
        self.assertEqual((pengajuan.status, pengajuan.minggu, pengajuan.verifikasi.count()), ('approved', W45, 3))
        pit, hauling = (AreaKerja.objects.get(pengajuan=pengajuan, jenis=j) for j in ('pit', 'hauling'))
        self.assertAlmostEqual(hauling.dipotong_ha, hauling.luas_ha, delta=0.2)  # half of it lay under the pit
        self.assertEqual(AreaKerja.objects.get(pengajuan=pengajuan, jenis='infra_tambang').sumber, 'tidak_ada')

        by_result = {(r['no_cctv_file'], r['hasil']) for r in rows}
        self.assertIn(('LMO-PM-9999', 'dilewati'), by_result)
        self.assertIn(('CS - Parkiran', 'dilewati'), by_result)       # coverage without a number
        self.assertIn(('CS - Parkiran', 'posisi diperbarui'), by_result)

    def test_running_again_skips_what_exists(self):
        self.run_import()
        rows = self.run_import()
        self.assertEqual(CctvCoverage.objects.count(), 1)
        self.assertEqual(PengajuanAreaKerja.objects.count(), 1)
        self.assertEqual(MitraSite.objects.filter(site=self.site, perusahaan=self.pama).count(), 1)
        notes = [r['keterangan'] for r in rows if r['hasil'] == 'dilewati']
        self.assertTrue(any('sudah ada di database' in n for n in notes))
