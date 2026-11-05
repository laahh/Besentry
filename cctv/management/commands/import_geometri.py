"""Import the first geometry from the site-contractor folders: CCTV positions, CCTV coverage, work areas.

    python manage.py import_geometri "/data/07_Data Trial" --laporan /data/laporan.csv [--dry-run]

Folders are named SITE_KONTRAKTOR_YYYY-Wnn (e.g. BMO2_PAMA_2026-W43) with sub-folders `point`, `coverage`
and `working_area`, each holding one shapefile. A folder without a week (BC_2026) holds its point and
coverage shapefiles directly, with the site in each feature; it gets --minggu-tanpa-week (default: the
latest week of the other folders). `DB CCTV ALL` is skipped.

    point         the CCTV position replaces the spreadsheet lat/lon
    coverage      CctvCoverage for the folder's week; several polygons of one CCTV are merged
    working_area  an Approved PengajuanAreaKerja for the folder's week, the work-area rules applied
                  (dissolved per kind, clipped by priority Pit > Hauling > Infra Tambang > Infra Non Tambang)

CCTV are matched by number (spaces and line breaks removed, BM01 -> BMO1, the site prefix added when it is
missing), then by name within the folder's site and company. What does not match, what already exists
(a coverage or work area for that CCTV / Mitra Site and week) and what looks wrong is skipped and listed
in the report; nothing in the database is overwritten. Everything runs in one transaction.
"""

import csv
import datetime
import re
from collections import defaultdict
from pathlib import Path

from django.contrib.gis.gdal import DataSource, GDALException, OGRGeometry, SpatialReference
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from areakerja import kalkulasi, services
from areakerja.models import (
    AreaKerja, Departemen, JenisArea, MitraSite, PengajuanAreaKerja, RiwayatAreaKerja, VerifikasiAreaKerja,
)
from cctv.coverage import LUAS_MAKS_HA, UTM_50N, _batas_site
from cctv.models import Cctv, CctvCoverage
from core.models import Perusahaan, Site

FOLDER = re.compile(r'^(?P<site>[A-Za-z]+\d?)(?:-[^_]+)?_(?P<kode>[A-Za-z]+)_(?P<tahun>\d{4})-W(?P<week>\d{1,2})$')
SITE = {'BMO1': 'BMO 1', 'BMO2': 'BMO 2', 'BMO3': 'BMO 3', 'GMO': 'GMO', 'LMO': 'LMO', 'SMO': 'SMO', 'PMO': 'PMO', 'HO': 'HO'}
KONTRAKTOR = {
    'BC': 'PT Berau Coal', 'KDC': 'PT Kaltim Diamond Coal', 'PAMA': 'PT Pamapersada Nusantara',
    'BAR': 'PT Bumi Artlantis Raya', 'BUMA': 'PT Bukit Makmur Mandiri Utama', 'FAD': 'PT Fajar Anugerah Dinamika',
    'MTN': 'PT Madhani Talatah Nusantara',
}
JENIS = {
    'PIT': JenisArea.PIT, 'HAULING': JenisArea.HAULING, 'INFRAS TAMBANG': JenisArea.INFRA_TAMBANG,
    'INFRA TAMBANG': JenisArea.INFRA_TAMBANG, 'INFRAS NON-TAMBANG': JenisArea.INFRA_NON_TAMBANG,
    'INFRA NON TAMBANG': JenisArea.INFRA_NON_TAMBANG, 'INFRAS NON TAMBANG': JenisArea.INFRA_NON_TAMBANG,
}
# Field names that hold the CCTV number, in the spellings the folders use.
NOMOR = ('no_cctv', 'no cctv_no', 'no_cctv_no', 'nomor_cctv', 'nomor cctv', 'no__cctv', 'gmo')
NAMA = ('nama_cctv', 'nama cctv', 'nama')
LOOKS_LIKE_NUMBER = re.compile(r'^[A-Z]+[0-9]*-[A-Z.]+-\d+')
SAME_POINT_M = 1.0


def isoweek_monday(year, week):
    return datetime.date.fromisocalendar(year, week, 1)


def clean(value):
    """A number or name as typed: the first non-empty line, trimmed."""
    if value is None:
        return ''
    lines = [line.strip() for line in str(value).replace('\r', '\n').split('\n')]
    return next((line for line in lines if line), '')


def raw_field(feature, names):
    """(cleaned value, value as stored) of the first of `names` the feature has and fills."""
    by_lower = {name.lower(): name for name in feature.fields}
    for name in names:
        if name in by_lower:
            raw = feature.get(by_lower[name])
            value = clean(raw)
            if value:
                return value, str(raw)
    return '', ''


def field(feature, names):
    return raw_field(feature, names)[0]


def to_wgs84(feature, assumed=UTM_50N):
    """The feature's geometry in EPSG:4326, without Z/M; a file without .prj is taken as UTM 50N."""
    ogr = OGRGeometry(feature.geom.wkt)
    ogr.set_3d(False)
    ogr.set_measured(False)
    srs = feature.geom.srs
    ogr.srs = srs if srs is not None else SpatialReference(assumed)
    ogr.transform(4326)
    return GEOSGeometry(ogr.wkb, srid=4326)


class Laporan:
    """The report: one row per thing skipped, corrected or imported."""

    COLUMNS = ['folder', 'file', 'baris', 'no_cctv_file', 'no_cctv_db', 'hasil', 'keterangan']

    def __init__(self):
        self.rows = []
        self.count = defaultdict(int)

    def add(self, folder, file, baris, nomor, cctv, hasil, keterangan=''):
        self.rows.append([folder, file, baris, nomor, cctv.no_cctv if cctv else '', hasil, keterangan])
        self.count[hasil] += 1

    def write(self, path):
        with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(self.COLUMNS)
            writer.writerows(self.rows)


class Command(BaseCommand):
    help = 'Impor posisi CCTV, coverage CCTV, dan area kerja dari folder shapefile site-kontraktor.'

    def add_arguments(self, parser):
        parser.add_argument('root', help='Folder induk berisi folder SITE_KONTRAKTOR_YYYY-Wnn.')
        parser.add_argument('--laporan', help='File CSV laporan (default: <root>/laporan_impor_geometri.csv).')
        parser.add_argument('--minggu-tanpa-week', help='Senin (YYYY-MM-DD) untuk folder tanpa week, mis. BC_2026.')
        parser.add_argument('--dry-run', action='store_true', help='Jalankan dan buat laporan, lalu batalkan semua perubahan.')

    def handle(self, root, laporan=None, minggu_tanpa_week=None, dry_run=False, **options):
        root = Path(root)
        if not root.is_dir():
            raise CommandError(f'Folder tidak ada: {root}')
        self.report = Laporan()
        folders = sorted(p for p in root.iterdir() if p.is_dir() and p.name != 'DB CCTV ALL')
        weeks = [self.week_of(p) for p in folders if FOLDER.match(p.name)]
        default_week = (datetime.date.fromisoformat(minggu_tanpa_week) if minggu_tanpa_week
                        else max(weeks) if weeks else None)
        self.batas, self.nama_batas = _batas_site()
        self.sites = {s.kode: s for s in Site.objects.all()}
        self.by_number = {c.no_cctv.upper(): c for c in Cctv.objects.select_related('site', 'perusahaan')}
        self.now = timezone.now()

        with transaction.atomic():
            self.seed_mitra_sites()
            for folder in folders:
                self.folder(folder, default_week)
            kalkulasi.hitung_live()
            if dry_run:
                transaction.set_rollback(True)

        path = Path(laporan) if laporan else root / 'laporan_impor_geometri.csv'
        self.report.write(path)
        summary = ', '.join(f'{k}: {v}' for k, v in sorted(self.report.count.items()))
        self.stdout.write(self.style.SUCCESS(f'{"[dry-run] " if dry_run else ""}Selesai. {summary}. Laporan: {path}'))

    # --- Folders -----------------------------------------------------------------------------------

    @staticmethod
    def week_of(folder):
        match = FOLDER.match(folder.name)
        return isoweek_monday(int(match['tahun']), int(match['week']))

    def seed_mitra_sites(self):
        """A Mitra Site for every site/company pair with CCTV (as migration 0002 does on an existing DB)."""
        pairs = Cctv.objects.filter(site__isnull=False, perusahaan__isnull=False).values_list('site_id', 'perusahaan_id').distinct()
        MitraSite.objects.bulk_create([MitraSite(site_id=s, perusahaan_id=p) for s, p in pairs], ignore_conflicts=True)

    def folder(self, folder, default_week):
        match = FOLDER.match(folder.name)
        if match:
            site = self.sites.get(SITE.get(match['site'].upper(), ''))
            kode = match['kode'].upper()
            minggu = self.week_of(folder)
        else:  # BC_2026: the site is in each feature
            site, kode, minggu = None, folder.name.split('_')[0].upper(), default_week
        nama = KONTRAKTOR.get(kode)
        perusahaan = Perusahaan.objects.filter(nama=nama).first() if nama else None
        if perusahaan is None or (match and site is None):
            self.report.add(folder.name, '', '', '', None, 'dilewati', 'Site atau kontraktor folder tidak dikenal')
            return
        ctx = {'folder': folder.name, 'site': site, 'perusahaan': perusahaan, 'minggu': minggu}
        for kind, shp in self.shapefiles(folder):
            ctx['file'] = shp.relative_to(folder).as_posix()
            try:
                layer = DataSource(str(shp))[0]
            except (GDALException, IndexError) as exc:
                self.report.add(folder.name, ctx['file'], '', '', None, 'dilewati', f'Shapefile tidak terbaca: {exc}')
                continue
            if kind == 'point':
                self.points(layer, ctx)
            elif kind == 'coverage':
                self.coverage(layer, ctx)
            else:
                self.work_area(layer, ctx)

    @staticmethod
    def shapefiles(folder):
        """[(kind, path)]: from the sub-folders, or by file name for a flat folder."""
        found = []
        for shp in sorted(folder.rglob('*.shp')):
            parts = [p.lower() for p in shp.relative_to(folder).parts]
            name = parts[-1]
            if len(parts) > 1:
                kind = {'point': 'point', 'coverage': 'coverage', 'working_area': 'area', 'area_kerja': 'area'}.get(parts[0])
            else:
                kind = 'coverage' if 'coverage' in name else 'area' if 'area' in name else 'point'
            if kind:
                found.append((kind, shp))
        return found

    # --- Matching CCTV -------------------------------------------------------------------------------

    def match(self, feature, ctx, site, by_name=False):
        """(Cctv or None, number as typed, correction note). `by_name`: without a number, match the
        name within the site and company (points only; a coverage without a number is skipped)."""
        nomor, stored = raw_field(feature, NOMOR)
        nama, stored_nama = raw_field(feature, NAMA)
        if not nomor and LOOKS_LIKE_NUMBER.match(nama.upper()):
            nomor, stored, nama = nama, stored_nama, ''  # BMO1_KDC: the number sits in the name column
        if nomor:
            typed = nomor
            key = nomor.upper().replace(' ', '')
            fixes = []
            if stored != nomor or key != nomor.upper():
                fixes.append('spasi/baris baru dihapus')
            if re.match(r'^BM0\d-', key):
                key = 'BMO' + key[3:]
                fixes.append('BM0 -> BMO')
            if key not in self.by_number and site is not None:
                prefix = site.kode.replace(' ', '')
                if not key.startswith(prefix) and f'{prefix}-{key}' in self.by_number:
                    key = f'{prefix}-{key}'
                    fixes.append(f'prefix {prefix} ditambahkan')
            cctv = self.by_number.get(key)
            note = ', '.join(fixes)
            if cctv is None:
                note = 'nomor tidak ada di database' + (f' (setelah: {note})' if note else '')
            return cctv, typed, note
        if nama and not by_name:
            return None, nama, 'tanpa nomor CCTV'
        if nama:
            rows = Cctv.objects.filter(nama__iexact=nama, perusahaan=ctx['perusahaan'])
            if site is not None:
                rows = rows.filter(site=site)
            rows = list(rows[:2])
            if len(rows) == 1:
                return rows[0], nama, 'dicocokkan lewat nama'
            return None, nama, 'nama tidak unik' if rows else 'nama tidak ditemukan'
        return None, '', 'tanpa nomor dan nama CCTV'

    def feature_site(self, feature, ctx):
        if ctx['site'] is not None:
            return ctx['site']
        value = field(feature, ('site',)).upper()
        return self.sites.get(value) or self.sites.get(SITE.get(value.replace(' ', ''), ''))

    # --- Points --------------------------------------------------------------------------------------

    def points(self, layer, ctx):
        seen = {}
        for number, feature in enumerate(layer, start=1):
            site = self.feature_site(feature, ctx)
            cctv, typed, note = self.match(feature, ctx, site, by_name=True)
            if cctv is None:
                self.report.add(ctx['folder'], ctx['file'], number, typed, None, 'dilewati', note)
                continue
            try:
                point = to_wgs84(feature)
            except GDALException as exc:
                self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'dilewati', f'geometri tidak terbaca: {exc}')
                continue
            if not isinstance(point, Point):
                self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'dilewati', f'bukan titik ({point.geom_type})')
                continue
            if cctv.pk in seen:
                far = seen[cctv.pk].transform(UTM_50N, clone=True).distance(point.transform(UTM_50N, clone=True))
                if far > SAME_POINT_M:
                    self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'dilewati',
                                    f'titik ganda untuk CCTV yang sama, berjarak {far:.0f} m; posisi pertama dipakai')
                continue
            seen[cctv.pk] = point
            if not self.batas.contains(point):
                self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'peringatan', f'titik di luar {self.nama_batas}')
            cctv.posisi = point
            cctv.save(update_fields=['posisi', 'diubah'])
            self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'posisi diperbarui', note)

    # --- Coverage ------------------------------------------------------------------------------------

    def coverage(self, layer, ctx):
        polygons = defaultdict(list)
        typed_of = {}
        for number, feature in enumerate(layer, start=1):
            site = self.feature_site(feature, ctx)
            cctv, typed, note = self.match(feature, ctx, site)
            if cctv is None:
                self.report.add(ctx['folder'], ctx['file'], number, typed, None, 'dilewati', note)
                continue
            try:
                geom = services._multi(to_wgs84(feature).make_valid())
            except GDALException:
                self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'dilewati', 'tanpa geometri')
                continue
            if geom is None:
                self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'dilewati', 'bukan polygon')
                continue
            polygons[cctv].append(geom)
            typed_of.setdefault(cctv, (number, typed, note))
        for cctv, parts in polygons.items():
            number, typed, note = typed_of[cctv]
            merged = parts[0]
            for part in parts[1:]:
                merged = merged.union(part)
            merged = services._multi(merged)
            notes = [n for n in (note, f'{len(parts)} polygon digabung' if len(parts) > 1 else '') if n]
            if CctvCoverage.objects.filter(cctv=cctv, minggu=ctx['minggu']).exists():
                self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'dilewati',
                                f'coverage {ctx["minggu"]:%G-W%V} sudah ada di database')
                continue
            luas = services.luas_ha(merged)
            if luas >= LUAS_MAKS_HA:
                notes.append(f'luas {luas:,.1f} ha melebihi {LUAS_MAKS_HA} ha')
            if not self.batas.contains(merged):
                notes.append(f'sebagian di luar {self.nama_batas}')
            CctvCoverage.objects.create(cctv=cctv, minggu=ctx['minggu'], geom=merged)
            self.report.add(ctx['folder'], ctx['file'], number, typed, cctv, 'coverage diimpor', '; '.join(notes))

    # --- Work areas ----------------------------------------------------------------------------------

    def work_area(self, layer, ctx):
        mitra_site, _ = MitraSite.objects.get_or_create(site=ctx['site'], perusahaan=ctx['perusahaan'])
        label = f'{mitra_site} {ctx["minggu"]:%G-W%V}'
        if PengajuanAreaKerja.objects.filter(mitra_site=mitra_site, minggu=ctx['minggu']).exists():
            self.report.add(ctx['folder'], ctx['file'], '', '', None, 'dilewati', f'area kerja {label} sudah ada di database')
            return
        by_kind = defaultdict(list)
        for number, feature in enumerate(layer, start=1):
            try:
                geom = to_wgs84(feature)
            except GDALException:
                self.report.add(ctx['folder'], ctx['file'], number, '', None, 'dilewati', 'tanpa geometri')
                continue
            area = services._multi(geom.make_valid()) if geom.geom_type in ('Polygon', 'MultiPolygon') else None
            if area is None:
                self.report.add(ctx['folder'], ctx['file'], number, '', None, 'dilewati', f'bukan polygon ({geom.geom_type})')
                continue
            jenis = JENIS.get(field(feature, ('area_kerja',)).upper().strip())
            if jenis is None:
                self.report.add(ctx['folder'], ctx['file'], number, '', None, 'dilewati',
                                f'jenis area tidak dikenal: {field(feature, ("area_kerja",))!r}')
                continue
            by_kind[jenis].append(area)
        if not by_kind:
            self.report.add(ctx['folder'], ctx['file'], '', '', None, 'dilewati', f'tidak ada polygon area kerja untuk {label}')
            return

        approved_at = timezone.make_aware(datetime.datetime.combine(ctx['minggu'], datetime.time(12, 0)))
        pengajuan = PengajuanAreaKerja.objects.create(
            mitra_site=mitra_site, minggu=ctx['minggu'], status=PengajuanAreaKerja.Status.APPROVED,
            catatan=f'Impor data awal dari folder {ctx["folder"]}', diajukan_pada=approved_at, disetujui_pada=approved_at,
        )
        notes = []
        for jenis in JenisArea.values:
            parts = by_kind.get(jenis)
            if not parts:
                AreaKerja.objects.create(pengajuan=pengajuan, jenis=jenis, sumber=AreaKerja.Sumber.TIDAK_ADA)
                continue
            dissolved = services._multi(MultiPolygon([p for g in parts for p in g], srid=4326).unary_union)
            if not self.batas.contains(dissolved):
                notes.append(f'{JenisArea(jenis).label} sebagian di luar {self.nama_batas}')
            AreaKerja.objects.create(pengajuan=pengajuan, jenis=jenis, sumber=AreaKerja.Sumber.UPLOAD,
                                     file_shp=ctx['file'][:255], jumlah_feature=len(parts), geom_upload=dissolved)
        services.clip_by_priority(PengajuanAreaKerja.objects.prefetch_related('area').get(pk=pengajuan.pk))
        for departemen in Departemen.values:
            VerifikasiAreaKerja.objects.create(pengajuan=pengajuan, departemen=departemen)
        RiwayatAreaKerja.objects.create(pengajuan=pengajuan, aksi=RiwayatAreaKerja.Aksi.DISETUJUI,
                                        catatan=f'Impor data awal ({ctx["folder"]})')
        counts = ', '.join(f'{JenisArea(j).label} {len(p)}' for j, p in by_kind.items())
        self.report.add(ctx['folder'], ctx['file'], '', '', None, 'area kerja diimpor',
                        f'{label}: {counts}' + (f'; {"; ".join(notes)}' if notes else ''))
