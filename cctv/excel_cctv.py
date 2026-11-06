"""The Excel template for adding many CCTV at once, and reading it back.

A filled row becomes the same data the "Tambah CCTV" form posts, so the forms check it by the same
rules (see cctv.upload_excel_views). Names people can read (site code, company name, 'Live View')
are turned into the ids and values the forms expect here. Passwords are not in the template: they
would sit unencrypted in a file, so IT fills them in per CCTV.
"""

import datetime
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.datavalidation import DataValidation

from core.models import DetailLokasi, Lokasi, Perusahaan, Site

from .models import Cctv, ControlRoom
from .pengajuan_forms import SCREENSHOT_MAX_MB

MAX_ROWS = 500
XLSX_MAX_UNZIPPED_MB = 50  # a small file must not expand into something that fills the memory
ZIP_MAX_UNZIPPED_MB = 300
SHEET_DATA = 'CCTV'
SHEET_OPTIONS = 'Pilihan'
SHEET_HELP = 'Petunjuk'
HEADER_FILL = PatternFill('solid', fgColor='24695C')
HEADER_FONT = Font(bold=True, color='FFFFFF')
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
YES = {'ya', 'y', 'true', '1'}
NO = {'tidak', 'n', 'false', '0'}


class ExcelError(Exception):
    """The whole file cannot be used."""


class ScreenshotError(Exception):
    """One screenshot in the zip cannot be used."""


@dataclass(frozen=True)
class Column:
    header: str
    name: str  # the form field, as posted
    kind: str = 'text'
    choices: tuple = ()
    required: bool = False
    default: str = ''
    width: int = 18


def _choice(header, name, choices, default=''):
    return Column(header, name, 'choice', tuple(choices), default=default)


COLUMNS = (
    Column('No. CCTV', 'no_cctv', required=True),
    Column('Nama CCTV', 'nama', required=True, width=30),
    Column('Site', 'site', 'site', required=True),
    Column('Perusahaan', 'perusahaan', 'perusahaan', required=True, width=32),
    _choice('Kategori', 'kategori', Cctv.Kategori.choices),
    _choice('Fungsi CCTV', 'fungsi', Cctv.Fungsi.choices),
    _choice('Bentuk instalasi', 'bentuk_instalasi', Cctv.Bentuk.choices),
    _choice('Jenis', 'jenis', Cctv.Jenis.choices),
    _choice('Status', 'status', Cctv.Status.choices, Cctv.Status.LIVE),
    _choice('Kondisi', 'kondisi', Cctv.Kondisi.choices, Cctv.Kondisi.BAIK),
    Column('Control room', 'control_room', 'control_room', width=26),
    Column('Lokasi', 'lokasi', 'lokasi', width=26),
    Column('Detail lokasi', 'detail_lokasi', 'detail_lokasi', width=26),
    Column('Lokasi pemasangan', 'lokasi_pemasangan', width=26),
    _choice('Kategori area tercapture', 'kategori_area', Cctv.Kekritisan.choices),
    _choice('Kategori aktivitas tercapture', 'kategori_aktivitas', Cctv.Kekritisan.choices),
    Column('Connected', 'connected', 'bool'),
    Column('Mirrored', 'mirrored', 'bool'),
    Column('Fitur auto alert', 'auto_alert', 'bool'),
    Column('Longitude', 'longitude', 'number'),
    Column('Latitude', 'latitude', 'number'),
    Column('Periode update', 'periode_update', 'date'),
    Column('Verifikasi petugas OCR', 'verifikasi_ocr'),
    Column('Keterangan', 'keterangan', width=30),
    Column('Brand', 'spec-brand'),
    Column('Model / spesifikasi zoom', 'spec-model', width=26),
    Column('Radius pengawasan (m)', 'spec-radius_m', 'number'),
    Column('Purpose', 'spec-purpose'),
    Column('Tinggi pasang (m)', 'spec-tinggi_pasang_m', 'number'),
    Column('Tilt (derajat)', 'spec-tilt_deg', 'number'),
    Column('Azimuth (derajat)', 'spec-azimuth_deg', 'number'),
)
AKSES_COLUMNS = (
    Column('Group / nama NVR', 'akses-nvr'),
    Column('No. channel', 'akses-channel'),
    Column('IP CCTV', 'akses-ip_cctv'),
    Column('IP NVR', 'akses-ip_nvr'),
    Column('Link akses', 'akses-link_akses', width=30),
    Column('Username', 'akses-username'),
    Column('Nama PRTG', 'akses-prtg_name'),
)
HELP_LINES = (
    'Satu baris untuk satu CCTV baru. Kolom bertanda * wajib diisi.',
    f'Maksimal {MAX_ROWS} baris per file.',
    'Pilih Site, Perusahaan, dan kolom pilihan lain dari daftar pada selnya.',
    'Status dan Kondisi boleh kosong: terisi Live View dan Baik.',
    'Longitude dan latitude diisi berpasangan, dalam derajat desimal (mis. 117.5012 dan 2.1534).',
    'Periode update berupa tanggal, mis. 01/11/2026.',
    'Password CCTV tidak diisi di sini. Isi per CCTV lewat halaman pengajuan.',
    'Baris yang punya tangkapan layar di file ZIP langsung lanjut ke digitasi; yang lain menjadi Draft IT.',
    'Nama file tangkapan layar di ZIP sama dengan No. CCTV, mis. LMO-FAD-0100.png.',
)


def columns_for(scope):
    """Connection details are only for those allowed to see them."""
    return (*COLUMNS, *AKSES_COLUMNS) if scope.can_see_akses else COLUMNS


def _key(value):
    return str(value).strip().upper()


# --- The template ------------------------------------------------------------------------------

def _scoped(queryset, ids):
    return queryset if ids is None else queryset.filter(pk__in=ids)


def _options(column, scope):
    if column.kind == 'choice':
        return [label for _, label in column.choices]
    if column.kind == 'bool':
        return ['Ya', 'Tidak']
    if column.kind == 'site':
        return list(_scoped(Site.objects.all(), scope.editable_ids('site_id')).values_list('kode', flat=True))
    if column.kind == 'perusahaan':
        companies = _scoped(Perusahaan.objects.all(), scope.editable_ids('perusahaan_id'))
        return list(companies.order_by('nama').values_list('nama', flat=True))
    sources = {'control_room': ControlRoom, 'lokasi': Lokasi, 'detail_lokasi': DetailLokasi}
    if column.kind in sources:
        names = sources[column.kind].objects.order_by('nama').values_list('nama', flat=True)
        return list(dict.fromkeys(names))
    return []


def _write_text(sheet, row, column, value):
    cell = sheet.cell(row=row, column=column, value=value)
    cell.data_type = 's'  # a name starting with '=' stays text, never a formula


def _add_dropdown(data_sheet, options_sheet, index, column, options):
    letter = get_column_letter(index)
    for row, option in enumerate(options, start=2):
        _write_text(options_sheet, row, index, option)
    # Master names change: warn about one outside the list, the upload reports it clearly anyway.
    strict = column.kind in ('choice', 'bool', 'site', 'perusahaan')
    rule = DataValidation(
        type='list', allow_blank=True, formula1=f"{SHEET_OPTIONS}!${letter}$2:${letter}${len(options) + 1}",
        showErrorMessage=True, errorStyle='stop' if strict else 'warning',
        errorTitle=column.header, error='Pilih dari daftar.',
    )
    rule.add(f'{letter}2:{letter}{MAX_ROWS + 1}')
    data_sheet.add_data_validation(rule)


def build_template(scope):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_DATA
    options_sheet = workbook.create_sheet(SHEET_OPTIONS)
    options_sheet.sheet_state = 'hidden'
    for index, column in enumerate(columns_for(scope), start=1):
        cell = sheet.cell(row=1, column=index, value=column.header + (' *' if column.required else ''))
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        sheet.column_dimensions[get_column_letter(index)].width = column.width
        options = _options(column, scope)
        if options:
            _add_dropdown(sheet, options_sheet, index, column, options)
    sheet.freeze_panes = 'C2'
    help_sheet = workbook.create_sheet(SHEET_HELP)
    help_sheet.column_dimensions['A'].width = 110
    help_sheet['A1'], help_sheet['A1'].font = 'Cara mengisi', Font(bold=True)
    for row, line in enumerate(HELP_LINES, start=2):
        help_sheet.cell(row=row, column=1, value=line)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


# --- Reading a filled template -----------------------------------------------------------------

class Masters:
    """Names to ids, loaded once per upload rather than once per row."""

    def __init__(self):
        self.sites = {_key(kode): pk for pk, kode in Site.objects.values_list('pk', 'kode')}
        self.companies = {}
        for pk, kode, nama in Perusahaan.objects.values_list('pk', 'kode', 'nama'):
            if kode:
                self.companies.setdefault(_key(kode), pk)
            self.companies[_key(nama)] = pk
        self.rooms = {(site, _key(nama)): pk for pk, site, nama in ControlRoom.objects.values_list('pk', 'site_id', 'nama')}
        self.places = {}
        for pk, site, nama in Lokasi.objects.values_list('pk', 'site_id', 'nama'):
            self.places.setdefault(_key(nama), []).append((pk, site))
        self.details = {(lokasi, _key(nama)): pk
                        for pk, lokasi, nama in DetailLokasi.objects.values_list('pk', 'lokasi_id', 'nama')}


def _as_text(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Ya' if value else 'Tidak'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value).strip()


def _as_date(text):
    for pattern in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f'tanggal "{text}" tidak dikenal (gunakan dd/mm/yyyy)')


def _as_choice(column, text):
    wanted = text.casefold()
    for value, label in column.choices:
        if wanted in (str(value).casefold(), str(label).casefold()):
            return value
    raise ValueError(f'"{text}" tidak ada di daftar pilihan')


def _as_flag(text):
    wanted = text.casefold()
    if wanted in YES:
        return 'on'
    if wanted in NO:
        return ''
    raise ValueError(f'"{text}" harus Ya atau Tidak')


def _lookup(masters_map, key, text):
    try:
        return masters_map[key]
    except KeyError:
        raise ValueError(f'"{text}" tidak ditemukan') from None


def _as_place(masters, text, site):
    candidates = masters.places.get(_key(text), [])
    if not candidates:
        raise ValueError(f'"{text}" tidak ditemukan')
    in_site = [pk for pk, place_site in candidates if place_site == site]
    if in_site:
        return in_site[0]
    if len(candidates) == 1:
        return candidates[0][0]
    raise ValueError(f'"{text}" ada di beberapa site, tidak ada yang cocok dengan site baris ini')


def _convert(column, text, data, masters):
    kind = column.kind
    if kind == 'choice':
        return _as_choice(column, text)
    if kind == 'bool':
        return _as_flag(text)
    if kind == 'date':
        return _as_date(text)
    if kind == 'site':
        return _lookup(masters.sites, _key(text), text)
    if kind == 'perusahaan':
        return _lookup(masters.companies, _key(text), text)
    if kind == 'control_room':
        return _lookup(masters.rooms, (_int(data.get('site')), _key(text)), text)
    if kind == 'lokasi':
        return _as_place(masters, text, _int(data.get('site')))
    if kind == 'detail_lokasi':
        return _lookup(masters.details, (_int(data.get('lokasi')), _key(text)), text)
    return text


def _int(value):
    return int(value) if value else None


@dataclass
class Row:
    baris: int
    data: QueryDict
    errors: list = field(default_factory=list)

    @property
    def no_cctv(self):
        return self.data.get('no_cctv', '')

    @property
    def nama(self):
        return self.data.get('nama', '')


def _read_row(baris, cells, columns, masters):
    data, errors = QueryDict(mutable=True), []
    for column in columns:
        text = _as_text(cells.get(column.header.casefold()))
        if not text:
            if column.default:
                data[column.name] = column.default
            continue
        try:
            value = _convert(column, text, data, masters)
        except ValueError as exc:
            errors.append(f'{column.header}: {exc}.')
            continue
        if value != '':
            data[column.name] = value
    return Row(baris, data, errors)


def _check_archive(upload):
    try:
        with zipfile.ZipFile(upload) as archive:
            size = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ExcelError('File harus berupa Excel .xlsx.') from exc
    if size > XLSX_MAX_UNZIPPED_MB * 1024 * 1024:
        raise ExcelError('Isi file Excel terlalu besar.')
    upload.seek(0)


def _rows_of(sheet, columns):
    """(line number, {header: value}) per filled row, stopping as soon as there are too many."""
    lines = sheet.iter_rows(values_only=True)
    headers = [str(value).replace('*', '').strip().casefold() if value is not None else ''
               for value in next(lines, ())]
    missing = [column.header for column in columns if column.required and column.header.casefold() not in headers]
    if missing:
        raise ExcelError(f'Kolom {", ".join(missing)} tidak ada. Gunakan template yang disediakan.')
    rows = []
    for number, values in enumerate(lines, start=2):
        if not any(_as_text(value) for value in values):
            continue
        if len(rows) == MAX_ROWS:
            raise ExcelError(f'File berisi lebih dari {MAX_ROWS} baris CCTV. Bagi menjadi beberapa file.')
        rows.append((number, dict(zip(headers, values))))
    return rows


def read_rows(upload, scope):
    """A filled template -> [Row]. Raises ExcelError when the whole file cannot be used."""
    _check_archive(upload)
    try:
        workbook = load_workbook(upload, read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ExcelError('File Excel tidak terbaca. Gunakan template yang disediakan.') from exc
    try:
        sheet = workbook[SHEET_DATA] if SHEET_DATA in workbook.sheetnames else workbook.worksheets[0]
        columns = columns_for(scope)
        lines = _rows_of(sheet, columns)
    finally:
        workbook.close()
    if not lines:
        raise ExcelError('Tidak ada baris CCTV di file.')
    masters = Masters()
    return [_read_row(number, cells, columns, masters) for number, cells in lines]


class Screenshots:
    """The optional zip of screenshots, named after the CCTV. Each is read only when its row is handled."""

    def __init__(self, upload=None):
        self.archive, self.members, self.used = None, {}, set()
        if not upload:
            return
        try:
            self.archive = zipfile.ZipFile(upload)
        except zipfile.BadZipFile as exc:
            raise ExcelError('File tangkapan layar harus berupa .zip.') from exc
        total = 0
        for info in self.archive.infolist():
            path = PurePosixPath(info.filename.replace('\\', '/'))
            if info.is_dir() or '__MACOSX' in path.parts or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            total += info.file_size
            self.members.setdefault(_key(path.stem), info)
        if total > ZIP_MAX_UNZIPPED_MB * 1024 * 1024:
            raise ExcelError(f'Isi ZIP tangkapan layar melebihi {ZIP_MAX_UNZIPPED_MB} MB.')

    def get(self, no_cctv):
        info = self.members.get(_key(no_cctv))
        if info is None:
            return None
        self.used.add(_key(no_cctv))
        if info.file_size > SCREENSHOT_MAX_MB * 1024 * 1024:
            raise ScreenshotError(f'Tangkapan layar {info.filename} melebihi {SCREENSHOT_MAX_MB} MB.')
        try:
            content = self.archive.read(info)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ScreenshotError(f'Tangkapan layar {info.filename} tidak terbaca.') from exc
        return SimpleUploadedFile(PurePosixPath(info.filename.replace('\\', '/')).name, content)

    @property
    def unused(self):
        return sorted(info.filename for key, info in self.members.items() if key not in self.used)

    def close(self):
        if self.archive:
            self.archive.close()
