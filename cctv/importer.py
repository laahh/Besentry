"""Import the CCTV spreadsheet (one tab per site/company) into the database.

Every site tab follows the same template; tabs differ only in their access columns
(BC tabs: NVR/channel/IP, mitra tabs: Link Akses). Columns are found by header name,
except column A, which is always the site (its header is blank or broken in some tabs).
"""

import datetime
import re
from dataclasses import dataclass, field

from django.contrib.gis.geos import Point
from django.db import connection, transaction

from core.models import DetailLokasi, Lokasi, Perusahaan, Site

from .models import Cctv, CctvAkses, CctvSpec, ControlRoom

# Tabs that look like a site tab but are summaries or outdated copies.
SKIPPED_SHEETS = {'List CCTV', 'List CCTV Problem', 'BUMA-Old', 'All BC'}
REQUIRED_HEADERS = {'no. cctv', 'fungsi cctv', 'kondisi'}
CHANGE_REASON = 'Import spreadsheet'

# Normalised header -> field key. A header matches when it starts with the given text.
HEADERS = (
    ('perusahaan', 'perusahaan'),
    ('kategori area', 'kategori_area'),
    ('kategori aktivitas', 'kategori_aktivitas'),
    ('kategori', 'kategori'),
    ('no. cctv', 'no_cctv'),
    ('nama cctv', 'nama'),
    ('no. channel', 'channel'),
    ('group nvr', 'nvr'),
    ('nama nvr', 'nvr'),
    ('fungsi cctv', 'fungsi'),
    ('bentuk instalasi', 'bentuk_instalasi'),
    ('jenis spesifikasi zoom', 'model'),
    ('jenis', 'jenis'),
    ('tipe cctv', 'brand'),
    ('radius pengawasan', 'radius_m'),
    ('lokasi pemasangan', 'lokasi_pemasangan'),
    ('control room', 'control_room'),
    ('status', 'status'),
    ('kondisi', 'kondisi'),
    ('longitude', 'longitude'),
    ('latitude', 'latitude'),
    ('id lokasi', 'lokasi_id'),
    ('coverage lokasi', 'lokasi_nama'),
    ('id detail lokasi', 'detail_lokasi_id'),
    ('coverage detail lokasi', 'detail_lokasi_nama'),
    ('ip cctv', 'ip_cctv'),
    ('ip nvr', 'ip_nvr'),
    ('link akses', 'link_akses'),
    ('user name', 'username'),
    ('password', 'password'),
    ('connected', 'connected'),
    ('mirrored', 'mirrored'),
    ('fitur auto alert', 'auto_alert'),
    ('keterangan', 'keterangan'),
    ('verifikasi by petugas ocr', 'verifikasi_ocr'),
    ('bulan update', 'bulan'),
    ('tahun update', 'tahun'),
    ('name prtg', 'prtg_name'),
)

KATEGORI = {'cctv support': 'support', 'mining eyes': 'mining_eyes', 'cctv portable': 'portable'}
FUNGSI = {'fixed view': 'fixed', 'patrol view': 'patrol', 'mobile view': 'mobile',
          'portable (ikk)': 'portable', 'portable': 'portable'}
BENTUK = {'statis': 'statis', 'dinamis': 'dinamis'}
JENIS = {'fix': 'fixed', 'fixed': 'fixed', 'ptz': 'ptz', 'mob': 'mobile', 'mobile': 'mobile',
         'portable': 'portable'}
STATUS = {'live view': 'live', 'live': 'live', 'offline': 'offline'}
KONDISI = {'baik': 'baik', 'breakdown': 'breakdown', 'repair': 'repair', 'dismantle': 'dismantle',
           'take out': 'take_out'}
BRANDS = {'hikvision': 'Hikvision', 'dahua': 'Dahua', 'ezviz': 'EZVIZ', 'ezvize': 'EZVIZ',
          'sony': 'Sony', 'axis': 'AXIS', 'g-lenz': 'G-Lenz'}
TRUE_VALUES = {'yes', 'y', 'ya', 'true', '1'}

CCTV_FIELDS = ('nama', 'kategori', 'fungsi', 'bentuk_instalasi', 'jenis', 'lokasi_pemasangan',
               'kategori_area', 'kategori_aktivitas', 'status', 'kondisi', 'connected', 'mirrored',
               'auto_alert', 'keterangan', 'verifikasi_ocr')
SPEC_FIELDS = ('brand', 'model', 'radius_m')
AKSES_FIELDS = ('nvr', 'channel', 'ip_cctv', 'ip_nvr', 'link_akses', 'username', 'password', 'prtg_name')


def clean(value):
    """Cell value as a single-spaced string ('' for empty). Whole floats lose their '.0'."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r'\s+', ' ', str(value)).strip()


def _key(text):
    return clean(text).lower()


def header_map(headers):
    """Column index -> field key; column 0 is always the site."""
    mapping = {0: 'site'}
    for index, header in enumerate(headers):
        name = _key(header)
        for prefix, key in HEADERS:
            if index and name.startswith(prefix) and key not in mapping.values():
                mapping[index] = key
                break
    return mapping


def is_site_sheet(title, headers):
    return title not in SKIPPED_SHEETS and REQUIRED_HEADERS <= {_key(h) for h in headers}


def to_float(value):
    try:
        return float(clean(value).replace(',', '.'))
    except ValueError:
        return None


def to_int(value):
    number = to_float(value)
    return int(number) if number is not None and number.is_integer() else None


def to_point(lon, lat):
    lon, lat = to_float(lon), to_float(lat)
    if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return Point(lon, lat, srid=4326)


def to_periode(bulan, tahun):
    bulan, tahun = to_int(bulan), to_int(tahun)
    if not bulan or not tahun or not 1 <= bulan <= 12:
        return None
    return datetime.date(tahun, bulan, 1)


def kekritisan(value):
    text = _key(value)
    if 'highrisk' in text or 'high risk' in text:
        return 'highrisk'
    if 'non' in text:
        return 'non_kritis'
    return 'kritis' if 'kritis' in text else ''


@dataclass
class Report:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    warnings: list = field(default_factory=list)

    def warn(self, where, message):
        self.warnings.append(f'{where}: {message}')


class Importer:
    def __init__(self):
        self.report = Report()
        self.sites = {s.kode.upper(): s for s in Site.objects.all()}
        self.perusahaan = {p.nama.lower(): p for p in Perusahaan.objects.all()}
        self.control_rooms = {(c.site_id, c.nama.lower()): c for c in ControlRoom.objects.all()}
        self.lokasi = set(Lokasi.objects.values_list('id', flat=True))
        self.detail_lokasi = set(DetailLokasi.objects.values_list('id', flat=True))
        # Existing rows are loaded once instead of one query per spreadsheet row.
        self.cctv = {c.no_cctv: c for c in Cctv.objects.all()}
        self.related = {
            CctvSpec: {s.cctv_id: s for s in CctvSpec.objects.all()},
            CctvAkses: {a.cctv_id: a for a in CctvAkses.objects.all()},
        }
        self.seen = set()

    # Lookups (cached, so each master row costs one query at most)

    def _choice(self, where, label, mapping, value):
        text = _key(value)
        if text and text not in mapping:
            self.report.warn(where, f'{label} "{clean(value)}" tidak dikenal, dikosongkan')
        return mapping.get(text, '')

    def _site(self, kode):
        kode = clean(kode).upper()
        if kode not in self.sites:
            self.sites[kode] = Site.objects.create(kode=kode)
        return self.sites[kode]

    def _perusahaan(self, nama, no_cctv):
        if nama.lower() not in self.perusahaan:
            parts = no_cctv.split('-')
            kode = parts[1] if len(parts) == 3 else ''
            self.perusahaan[nama.lower()] = Perusahaan.objects.create(nama=nama, kode=kode[:20])
        return self.perusahaan[nama.lower()]

    def _control_room(self, site, nama):
        if not nama:
            return None
        key = (site.pk, nama.lower())
        if key not in self.control_rooms:
            self.control_rooms[key] = ControlRoom.objects.create(site=site, nama=nama)
        return self.control_rooms[key]

    def _lokasi(self, row, site):
        lokasi_id = to_int(row.get('lokasi_id'))
        if lokasi_id is None:
            return None, None
        if lokasi_id not in self.lokasi:
            Lokasi.objects.create(id=lokasi_id, nama=clean(row.get('lokasi_nama')) or str(lokasi_id), site=site)
            self.lokasi.add(lokasi_id)
        detail_id = to_int(row.get('detail_lokasi_id'))
        if detail_id is not None and detail_id not in self.detail_lokasi:
            DetailLokasi.objects.create(
                id=detail_id, lokasi_id=lokasi_id,
                nama=clean(row.get('detail_lokasi_nama')) or str(detail_id),
            )
            self.detail_lokasi.add(detail_id)
        return lokasi_id, detail_id

    # Rows

    def import_row(self, sheet, number, row):
        where = f'{sheet} baris {number}'
        no_cctv = clean(row.get('no_cctv'))
        site_kode, perusahaan = clean(row.get('site')), clean(row.get('perusahaan'))
        if not no_cctv or not site_kode or not perusahaan:
            self.report.skipped += 1
            return
        if no_cctv in self.seen:
            self.report.warn(where, f'{no_cctv} duplikat, baris ini menimpa baris sebelumnya')
        self.seen.add(no_cctv)

        site = self._site(site_kode)
        lokasi_id, detail_id = self._lokasi(row, site)
        control_room = self._control_room(site, clean(row.get('control_room'))[:150])
        # *_id keys: comparing ids avoids loading each related object.
        values = {
            'nama': clean(row.get('nama'))[:150] or no_cctv,
            'site_id': site.pk,
            'perusahaan_id': self._perusahaan(perusahaan, no_cctv).pk,
            'kategori': self._choice(where, 'Kategori', KATEGORI, row.get('kategori')),
            'fungsi': self._choice(where, 'Fungsi', FUNGSI, row.get('fungsi')),
            'bentuk_instalasi': self._choice(where, 'Bentuk', BENTUK, row.get('bentuk_instalasi')),
            'jenis': self._choice(where, 'Jenis', JENIS, row.get('jenis')),
            'lokasi_pemasangan': clean(row.get('lokasi_pemasangan'))[:200],
            'control_room_id': control_room.pk if control_room else None,
            'lokasi_id': lokasi_id,
            'detail_lokasi_id': detail_id,
            'kategori_area': kekritisan(row.get('kategori_area')),
            'kategori_aktivitas': kekritisan(row.get('kategori_aktivitas')),
            'status': self._choice(where, 'Status', STATUS, row.get('status')) or Cctv.Status.LIVE,
            'kondisi': self._choice(where, 'Kondisi', KONDISI, row.get('kondisi')) or Cctv.Kondisi.BAIK,
            'posisi': to_point(row.get('longitude'), row.get('latitude')),
            'connected': _key(row.get('connected')) in TRUE_VALUES,
            'mirrored': _key(row.get('mirrored')) in TRUE_VALUES,
            'auto_alert': _key(row.get('auto_alert')) in TRUE_VALUES,
            'keterangan': clean(row.get('keterangan')),
            'verifikasi_ocr': clean(row.get('verifikasi_ocr'))[:255],
            'periode_update': to_periode(row.get('bulan'), row.get('tahun')),
        }
        cctv = self.cctv.get(no_cctv) or Cctv(no_cctv=no_cctv)
        is_new = cctv.pk is None
        self._save(cctv, values)
        self.cctv[no_cctv] = cctv

        brand = clean(row.get('brand'))
        self._save_related(CctvSpec, cctv, {
            'brand': BRANDS.get(brand.lower(), brand)[:50],
            'model': clean(row.get('model'))[:150],
            'radius_m': to_float(row.get('radius_m')),
        })
        # Passwords keep their line breaks (e.g. "pass\nverif code: ...").
        password = str(row.get('password') or '').strip()
        self._save_related(CctvAkses, cctv, {
            'nvr': clean(row.get('nvr'))[:100],
            'channel': clean(row.get('channel'))[:20],
            'ip_cctv': clean(row.get('ip_cctv'))[:255],
            'ip_nvr': clean(row.get('ip_nvr'))[:255],
            'link_akses': clean(row.get('link_akses'))[:500],
            'username': clean(row.get('username'))[:150],
            'password': password,
            'prtg_name': clean(row.get('prtg_name'))[:150],
        })
        if is_new:
            self.report.created += 1
        else:
            self.report.updated += 1

    @staticmethod
    def _save(instance, values):
        changed = instance.pk is None
        for name, value in values.items():
            if getattr(instance, name) != value:
                setattr(instance, name, value)
                changed = True
        if changed:  # unchanged rows write no history
            instance._change_reason = CHANGE_REASON
            instance.save()

    def _save_related(self, model, cctv, values):
        if not any(v not in ('', None) for v in values.values()):
            return
        existing = self.related[model]
        instance = existing.get(cctv.pk) or model(cctv=cctv)
        self._save(instance, values)
        existing[cctv.pk] = instance

    def import_sheet(self, title, rows):
        """`rows` yields lists of cell values; the first one is the header."""
        rows = iter(rows)
        headers = next(rows, [])
        mapping = header_map(headers)
        for number, cells in enumerate(rows, start=2):
            row = {key: cells[index] for index, key in mapping.items() if index < len(cells)}
            self.import_row(title, number, row)


def reset_sequences():
    """Imported Lokasi ids are explicit; let new rows continue after the highest one."""
    with connection.cursor() as cursor:
        for model in (Lokasi, DetailLokasi):
            table = model._meta.db_table
            cursor.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
            )


def import_workbook(workbook, only=()):
    """Import every site tab of an openpyxl workbook. Returns (Report, imported tab names)."""
    imported = []
    # Both databases, so a failed import leaves no orphan history rows.
    with transaction.atomic(), transaction.atomic(using='log'):
        importer = Importer()
        for sheet in workbook.worksheets:
            if only and sheet.title not in only:
                continue
            rows = sheet.iter_rows(values_only=True)
            headers = next(rows, ())
            if not is_site_sheet(sheet.title, headers):
                continue
            importer.import_sheet(sheet.title, [headers, *rows])
            imported.append(sheet.title)
        reset_sequences()
    return importer.report, imported
