"""CCTV change requests: IT -> Survey Mitra -> Survey BC -> Safety BC, one CCTV per request.

    save_draft()        Draft IT / Return to IT: IT's form is kept, nothing reaches the CCTV yet
    continue_request()  IT's changes reach the CCTV. No geometry change: Approved at once.
                        Otherwise Waiting Survey Mitra, labelled with this week.
    save_digitasi()     Survey Mitra's boundary (drawn or SHP); kirim -> Waiting Survey BC
    review_sgi()        Survey BC: -> Waiting Safety BC (whole checklist) / Return to Survey Mitra / Return to IT
    review_safety()     Safety BC: -> Approved (whole checklist) / Approved with warning / Return to IT / Return to SGI

There is no cancelling: every request runs until it is approved.
"""

import datetime
import tempfile
import zipfile

from django.contrib.gis.gdal import DataSource, GDALException, OGRGeometry
from django.contrib.gis.geos import GEOSException, GEOSGeometry, MultiPolygon, Point, Polygon
from django.db import transaction
from django.utils import timezone

from .models import CctvCoverage, PengajuanCoverage, RiwayatPengajuan

UTM_50N = 32650  # metres, for area and distance around Berau
# Rough extent of the Berau operations, used while no orthophoto is published.
WILAYAH = Polygon.from_bbox((115.5, 0.5, 119.5, 3.5))
WILAYAH.srid = 4326
LUAS_MAKS_HA = 200
JARAK_DEFAULT_M = 2000
JARAK_FAKTOR = 1.5  # allowed distance from the camera = radius x this
MOVE_THRESHOLD_M = 1  # GPS jitter below this is not a move
# A change to any of these sends the request through digitisation; anything else is approved at once.
GEOMETRY_FIELDS = ('posisi', 'jenis')
GEOMETRY_SPEC_FIELDS = ('radius_m', 'azimuth_deg', 'tilt_deg', 'tinggi_pasang_m')
SHP_PARTS = ('.shp', '.shx', '.dbf', '.prj')
SHP_MAX_MB = 50
SHP_MAX_FEATURES = 500
SHP_CCTV_COLUMN = 'no_cctv'  # the upload of many boundaries at once: which CCTV each feature is for
# More vertices than this are simplified on load, so the polygon stays editable in the browser.
EDIT_MAX_VERTICES = 300
SIMPLIFY_TOLERANCES_M = (0.1, 0.25, 0.5, 1, 2, 5)

Status = PengajuanCoverage.Status
Aksi = RiwayatPengajuan.Aksi


class GeometryError(ValueError):
    pass


class ReviewError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def this_monday():
    today = timezone.localdate()
    return today - datetime.timedelta(days=today.weekday())


def open_request(cctv):
    return PengajuanCoverage.objects.filter(cctv=cctv).exclude(status__in=PengajuanCoverage.SELESAI).first()


def latest_request(cctv):
    return PengajuanCoverage.objects.filter(cctv=cctv).order_by('-dibuat', '-pk').first()


def previous_request(pengajuan):
    """The last approved request of the same CCTV with a screenshot: what the camera saw before."""
    if not pengajuan.cctv_id:
        return None
    return (PengajuanCoverage.objects.filter(cctv_id=pengajuan.cctv_id, status__in=PengajuanCoverage.SELESAI)
            .exclude(pk=pengajuan.pk).exclude(tangkapan_layar='').order_by('-dibuat', '-pk').first())


def approved_coverage(cctv):
    """(geometry or None, number of approved versions)."""
    rows = CctvCoverage.objects.filter(cctv=cctv)
    latest = rows.order_by('-minggu').only('geom').first()
    return (latest.geom if latest else None), rows.count()


def map_coverage(cctv):
    """The boundary the map shows: Survey Mitra's latest one while a request is open, else the approved one."""
    request = open_request(cctv)
    if request and request.geom:
        return request.geom
    return approved_coverage(cctv)[0]


def luas_ha(geom):
    return geom.transform(UTM_50N, clone=True).area / 10_000 if geom else None


# --- Geometry ----------------------------------------------------------------------------------

def geometry_state(cctv):
    """The fields that decide whether a change needs a new boundary."""
    spec = getattr(cctv, 'spec', None)
    return {
        'posisi': cctv.posisi,
        'jenis': cctv.jenis,
        **{name: getattr(spec, name, None) for name in GEOMETRY_SPEC_FIELDS},
    }


def _moved(old, new):
    if old is None or new is None:
        return old != new
    return old.transform(UTM_50N, clone=True).distance(new.transform(UTM_50N, clone=True)) >= MOVE_THRESHOLD_M


GEOMETRY_LABELS = {
    'posisi': 'Koordinat', 'jenis': 'Jenis', 'radius_m': 'Radius (m)', 'azimuth_deg': 'Azimuth (°)',
    'tilt_deg': 'Tilt (°)', 'tinggi_pasang_m': 'Tinggi pasang (m)',
}


def _shown(value):
    if value is None or value == '':
        return '-'
    if isinstance(value, Point):
        return f'{value.y:.6f}, {value.x:.6f}'
    return str(value)


def geometry_changes(before, after):
    """[[label, before, after]] for the geometry fields that changed; empty = nothing to digitise."""
    changes = []
    for name, label in GEOMETRY_LABELS.items():
        changed = _moved(before[name], after[name]) if name == 'posisi' else before[name] != after[name]
        if changed:
            changes.append([label, _shown(before[name]), _shown(after[name])])
    return changes


def parse_geometry(text):
    """GeoJSON (Polygon/MultiPolygon, or a Feature of one) -> MultiPolygon in EPSG:4326."""
    if not text:
        return None
    try:
        geom = GEOSGeometry(text, srid=4326)
    except (GEOSException, ValueError, TypeError) as exc:
        raise GeometryError('Polygon tidak terbaca.') from exc
    if isinstance(geom, Polygon):
        geom = MultiPolygon(geom, srid=4326)
    if not isinstance(geom, MultiPolygon):
        raise GeometryError('Coverage harus berupa polygon.')
    return geom


def _is_utm_50n(srs):
    try:
        srs.identify_epsg()
    except GDALException:
        pass
    if srs.srid == UTM_50N:
        return True
    name = (srs.name or '').upper().replace(' ', '_')
    return 'UTM' in name and 'ZONE_50N' in name and 'WGS' in name


def read_shp_zip(upload):
    """A zipped shapefile with exactly one polygon in UTM 50N -> MultiPolygon in EPSG:4326."""
    return _from_zip(upload, _read_shapefile)


def read_features_zip(upload):
    """A zipped shapefile with one polygon per CCTV -> [{'no_cctv', 'geom', 'error'}].

    A problem with the whole file raises GeometryError; a problem with one feature is its 'error'.
    """
    return _from_zip(upload, _read_features)


def _from_zip(upload, read):
    try:
        archive = zipfile.ZipFile(upload)
    except zipfile.BadZipFile as exc:
        raise GeometryError('File harus berupa .zip berisi shapefile.') from exc
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if sum(m.file_size for m in members) > SHP_MAX_MB * 1024 * 1024:
            raise GeometryError(f'Isi zip lebih dari {SHP_MAX_MB} MB.')
        shapes = [m for m in members if m.filename.lower().endswith('.shp')]
        if len(shapes) != 1:
            raise GeometryError(f'Zip harus berisi tepat 1 file .shp (ada {len(shapes)}).')
        stem = shapes[0].filename[:-4].lower()
        parts = {ext: next((m for m in members if m.filename.lower() == stem + ext), None) for ext in SHP_PARTS}
        missing = [ext for ext, member in parts.items() if member is None]
        if missing:
            raise GeometryError(f'Shapefile tidak lengkap, tidak ada {", ".join(missing)}.')
        # On Windows a refused file is still open in GDAL while the error unwinds; it must stay that error.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            # Written under fixed names: no path from the zip ever reaches the disk.
            for ext, member in parts.items():
                with archive.open(member) as source, open(f'{folder}/boundary{ext}', 'wb') as target:
                    target.write(source.read())
            return read(f'{folder}/boundary.shp')


def _open_layer(path):
    try:
        return DataSource(path)[0]
    except (GDALException, IndexError) as exc:
        raise GeometryError('Shapefile tidak terbaca.') from exc


def _check_crs(layer):
    if layer.srs is None or not _is_utm_50n(layer.srs):
        raise GeometryError('CRS shapefile harus WGS 84 / UTM zone 50N (EPSG:32650).')


def _read_shapefile(path):
    layer = _open_layer(path)
    if layer.num_feat != 1:
        raise GeometryError(f'Shapefile harus berisi tepat 1 feature (ada {layer.num_feat}).')
    _check_crs(layer)
    return _feature_geom(layer[0])


def _read_features(path):
    layer = _open_layer(path)
    if layer.num_feat == 0:
        raise GeometryError('Shapefile tidak berisi feature.')
    if layer.num_feat > SHP_MAX_FEATURES:
        raise GeometryError(f'Shapefile berisi {layer.num_feat} feature (maksimal {SHP_MAX_FEATURES}).')
    _check_crs(layer)
    column = next((name for name in layer.fields if name.lower() == SHP_CCTV_COLUMN), None)
    if column is None:
        raise GeometryError(f'Shapefile harus punya kolom {SHP_CCTV_COLUMN} berisi nomor CCTV.')
    features = []
    for feature in layer:
        no_cctv = str(feature.get(column) or '').strip()
        try:
            features.append({'no_cctv': no_cctv, 'geom': _feature_geom(feature), 'error': ''})
        except (GeometryError, GDALException, GEOSException) as exc:
            features.append({'no_cctv': no_cctv, 'geom': None, 'error': str(exc)})
    return features


def _feature_geom(feature):
    ogr = feature.geom
    # Base type without the Z/M flags (3 = polygon, 6 = multipolygon).
    if (ogr.geom_type.num & 0x7FFFFFFF) % 1000 not in (3, 6):
        raise GeometryError(f'Feature harus berupa polygon (bukan {ogr.geom_type.name}).')
    flat = OGRGeometry(ogr.wkt)
    flat.set_3d(False)
    flat.set_measured(False)
    geom = GEOSGeometry(flat.wkb, srid=UTM_50N).transform(4326, clone=True)
    return MultiPolygon(geom, srid=4326) if isinstance(geom, Polygon) else geom


def _batas_site():
    """Where a boundary may lie: the active orthophoto's extent, else the Berau operations."""
    from orthophoto.models import Orthophoto
    ortho = Orthophoto.objects.filter(aktif=True).only('batas').first()
    return (ortho.batas, 'extent orthophoto') if ortho and ortho.batas else (WILAYAH, 'wilayah operasi Berau')


def editable_polygon(geom):
    """One polygon without holes, simplified when it has too many vertices to edit by hand.

    Returns (Polygon in EPSG:4326, note for the user or '').
    """
    polygon = geom[0] if isinstance(geom, MultiPolygon) and len(geom) == 1 else geom
    if not isinstance(polygon, Polygon):
        raise GeometryError(f'SHP berisi {len(geom)} polygon terpisah; gabungkan menjadi 1 polygon.')
    if polygon.num_interior_rings:
        raise GeometryError('Polygon SHP memiliki lubang (hole); hapus lubangnya agar bisa diedit di peta.')
    count = len(polygon.exterior_ring) - 1
    if count <= EDIT_MAX_VERTICES:
        return polygon, ''
    local = polygon.transform(UTM_50N, clone=True)
    for tolerance in SIMPLIFY_TOLERANCES_M:
        simple = local.simplify(tolerance, preserve_topology=True)
        if len(simple.exterior_ring) - 1 <= EDIT_MAX_VERTICES:
            break
    simple.transform(4326)
    return simple, (f'Polygon disederhanakan dari {count} ke {len(simple.exterior_ring) - 1} titik '
                    f'(toleransi {tolerance:g} m) agar bisa diedit.')


def validate_geometry(geom, cctv):
    """The automatic check before Survey BC sees a boundary. Returns a short summary."""
    if geom is None or geom.empty:
        raise GeometryError('Gambar polygon coverage di peta atau upload SHP.')
    if not geom.valid:
        raise GeometryError(f'Polygon tidak valid ({geom.valid_reason}). Pastikan garis tidak saling memotong.')
    batas, nama_batas = _batas_site()
    if not batas.contains(geom):
        raise GeometryError(f'Polygon berada di luar batas site ({nama_batas}).')
    luas = luas_ha(geom)
    if luas >= LUAS_MAKS_HA:
        raise GeometryError(f'Luas {luas:,.1f} ha terlalu besar (harus kurang dari {LUAS_MAKS_HA} ha).')

    summary = f'luas {luas:,.2f} ha'
    if cctv.posisi:
        batas_m = jangkauan_m(cctv)
        local = geom.transform(UTM_50N, clone=True)
        camera = cctv.posisi.transform(UTM_50N, clone=True)
        jarak = max(Point(x, y, srid=UTM_50N).distance(camera) for x, y in local.convex_hull.coords[0])
        if jarak > batas_m:
            raise GeometryError(f'Titik terjauh polygon {jarak:,.0f} m dari kamera, melebihi batas {batas_m:,.0f} m.')
        summary += f', titik terjauh {jarak:,.0f} m dari kamera'
    return summary


def jangkauan_m(cctv):
    """How far the camera is expected to see: its radius x JARAK_FAKTOR, or the default."""
    spec = getattr(cctv, 'spec', None)
    return spec.radius_m * JARAK_FAKTOR if spec and spec.radius_m else JARAK_DEFAULT_M


# --- The flow ----------------------------------------------------------------------------------

def _log(pengajuan, aksi, user=None, catatan=''):
    RiwayatPengajuan.objects.create(pengajuan=pengajuan, aksi=aksi, user=user, catatan=catatan)


def _expect(pengajuan, *statuses):
    if pengajuan.status not in statuses:
        raise ReviewError(f'Pengajuan ini sedang {pengajuan.get_status_display()}, bukan di tahap ini.')


def save_draft(pengajuan, user, data):
    """Keep IT's form as typed (no password) without touching the CCTV."""
    _expect(pengajuan, Status.DRAFT_IT, Status.RETURN_IT)
    pengajuan.data_draft = data
    if not pengajuan.diajukan_oleh_id:
        pengajuan.diajukan_oleh = user
    pengajuan.save()
    _log(pengajuan, Aksi.DRAFT, user)
    return pengajuan


@transaction.atomic
def continue_request(pengajuan, user, cctv, before):
    """IT continues to digitisation; `cctv` is already saved, `before` is geometry_state() from
    before saving (None for a new CCTV)."""
    _expect(pengajuan, Status.DRAFT_IT, Status.RETURN_IT)
    first = pengajuan.status == Status.DRAFT_IT
    pengajuan.cctv = cctv
    pengajuan.data_draft = {}
    pengajuan.minggu = this_monday()
    pengajuan.checklist_sgi, pengajuan.checklist_safety = [], []
    if not pengajuan.diajukan_oleh_id:
        pengajuan.diajukan_oleh = user
    if first:
        approved, versi = approved_coverage(cctv)
        pengajuan.posisi_sebelum = before['posisi'] if before else None
        pengajuan.geom_sebelum = approved
        pengajuan.versi = versi + 1
    changes = geometry_changes(before, geometry_state(cctv)) if before is not None else []
    if first or changes:  # a resubmission without new changes keeps the first list
        pengajuan.perubahan = changes
    if first:
        if before is not None and not pengajuan.perubahan:
            pengajuan.jenis = PengajuanCoverage.Jenis.UBAH
            pengajuan.status = Status.APPROVED
            pengajuan.selesai = timezone.now()
            pengajuan.save()
            _log(pengajuan, Aksi.OTOMATIS)
            return pengajuan
        pengajuan.jenis = PengajuanCoverage.Jenis.BARU if before is None else PengajuanCoverage.Jenis.DIPINDAH
    pengajuan.status = Status.MENUNGGU_MITRA
    pengajuan.save()
    _log(pengajuan, Aksi.DIAJUKAN, user, '' if first else 'diajukan ulang setelah dikembalikan')
    return pengajuan


@transaction.atomic
def save_digitasi(pengajuan, user, geom, *, kirim, sumber='', file_shp='', catatan='', orthophoto=None):
    """Survey Mitra's boundary: kept as a draft (already on the map), or checked and sent to Survey BC."""
    _expect(pengajuan, Status.MENUNGGU_MITRA, Status.RETURN_MITRA)
    pengajuan.geom = geom
    pengajuan.sumber_geom = sumber if geom else ''
    pengajuan.file_shp = file_shp if sumber == PengajuanCoverage.SumberGeom.SHP else ''
    pengajuan.digitasi_oleh = user
    pengajuan.orthophoto = orthophoto or pengajuan.orthophoto
    if not kirim:
        pengajuan.save()
        return pengajuan
    summary = validate_geometry(geom, pengajuan.cctv)  # raises GeometryError
    pengajuan.status = Status.MENUNGGU_SGI
    pengajuan.digitasi_pada = timezone.now()
    pengajuan.checklist_sgi = []
    pengajuan.save()
    _log(pengajuan, Aksi.DIGITASI, user, f'v{pengajuan.versi}' + (f' · {catatan}' if catatan else ''))
    _log(pengajuan, Aksi.VALIDASI, None, summary)
    return pengajuan


def _checked(items, checklist):
    wanted = set(checklist)
    return [key for key, _ in items if key in wanted]


RETURNS = {
    'it': (Status.RETURN_IT, 'ke IT'),
    'mitra': (Status.RETURN_MITRA, 'ke Survey Mitra'),
    'sgi': (Status.RETURN_SGI, 'ke Survey BC'),
}


def _return(pengajuan, user, ke, catatan, tahap):
    if not catatan:
        raise ReviewError('Catatan wajib diisi jika dikembalikan.')
    status, label = RETURNS[ke]
    pengajuan.status = status
    pengajuan.save()
    _log(pengajuan, Aksi.DIKEMBALIKAN, user, f'{tahap} {label}: {catatan}')
    return pengajuan


@transaction.atomic
def review_sgi(pengajuan, user, *, aksi, checklist, catatan=''):
    """aksi: 'safety' (forward; the whole checklist), 'mitra' or 'it' (return, with a note)."""
    _expect(pengajuan, Status.MENUNGGU_SGI, Status.RETURN_SGI)
    catatan = catatan.strip()
    pengajuan.checklist_sgi = _checked(PengajuanCoverage.CHECKLIST_SGI, checklist)
    if aksi in ('mitra', 'it'):
        return _return(pengajuan, user, aksi, catatan, 'Survey BC')
    if aksi != 'safety':
        raise ReviewError('Pilih tindakan review.')
    if len(pengajuan.checklist_sgi) != len(PengajuanCoverage.CHECKLIST_SGI):
        raise ReviewError('Checklist belum lengkap: kembalikan ke Survey Mitra untuk diperbaiki.')
    pengajuan.status = Status.MENUNGGU_SAFETY
    pengajuan.checklist_safety = []
    pengajuan.save()
    _log(pengajuan, Aksi.DISETUJUI_SGI, user, catatan)
    return pengajuan


@transaction.atomic
def review_safety(pengajuan, user, *, aksi, checklist, catatan=''):
    """aksi: 'approve' (Approved, or Approved with warning when the checklist is not whole),
    'it' or 'sgi' (return). Feedback is always required."""
    _expect(pengajuan, Status.MENUNGGU_SAFETY)
    catatan = catatan.strip()
    pengajuan.checklist_safety = _checked(PengajuanCoverage.CHECKLIST_SAFETY, checklist)
    if aksi in ('it', 'sgi'):
        return _return(pengajuan, user, aksi, catatan, 'Safety BC')
    if aksi != 'approve':
        raise ReviewError('Pilih tindakan review.')
    if not catatan:
        raise ReviewError('Feedback Safety wajib diisi.')
    lengkap = len(pengajuan.checklist_safety) == len(PengajuanCoverage.CHECKLIST_SAFETY)
    pengajuan.status = Status.APPROVED if lengkap else Status.APPROVED_WARNING
    pengajuan.feedback_safety = catatan
    pengajuan.selesai = timezone.now()
    pengajuan.save()
    CctvCoverage.objects.update_or_create(
        cctv=pengajuan.cctv, minggu=pengajuan.minggu, defaults={'geom': pengajuan.geom},
    )
    _log(pengajuan, Aksi.DISETUJUI_SAFETY if lengkap else Aksi.DISETUJUI_WARNING, user, catatan)
    return pengajuan
