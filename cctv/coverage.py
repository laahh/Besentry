"""Coverage change requests, reviewed by SGI and then Safety; approval activates the polygon.

Two ways in:
    create_digitasi_task() -> Menunggu digitasi   IT added or moved a CCTV (automatic, no polygon yet)
        save_digitasi(kirim=True) -> Menunggu Safety   SGI drew the polygon (geometry check passed)
    submit()               -> Menunggu SGI        IT drew the polygon themselves
        review() at SGI    -> Menunggu Safety, or Dikembalikan to IT; resubmit() -> Menunggu SGI

    review() at Safety     -> Selesai: the polygon becomes this week's CctvCoverage
                           -> or back to Menunggu digitasi: SGI redraws it
"""

import datetime
import hashlib
from dataclasses import dataclass

from django.contrib.gis.geos import GEOSException, GEOSGeometry, MultiPolygon, Point, Polygon
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from . import homography
from .models import CctvCoverage, KalibrasiKamera, PengajuanCoverage, RiwayatPengajuan

UTM_50N = 32650  # metres, for area and distance around Berau
# Rough extent of the Berau operations; anything outside is a digitising mistake.
WILAYAH = Polygon.from_bbox((115.5, 0.5, 119.5, 3.5))
WILAYAH.srid = 4326
LUAS_MAKS_HA = 1000
JARAK_DEFAULT_M = 2000
JARAK_FAKTOR = 1.5  # allowed distance from the camera = radius x this
Status = PengajuanCoverage.Status
Aksi = RiwayatPengajuan.Aksi


class GeometryError(ValueError):
    pass


def active_coverage(cctv):
    """(geometry or None, version number). Version counts every activation so far."""
    rows = CctvCoverage.objects.filter(cctv=cctv)
    latest = rows.order_by('-minggu').only('geom').first()
    return (latest.geom if latest else None), rows.count()


def luas_ha(geom):
    return geom.transform(UTM_50N, clone=True).area / 10_000 if geom else None


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


def validate_geometry(geom, cctv):
    """The automatic check before a request reaches SGI. Returns a short summary."""
    if geom is None or geom.empty:
        raise GeometryError('Gambar polygon coverage di peta.')
    if not geom.valid:
        raise GeometryError(f'Polygon tidak valid ({geom.valid_reason}). Pastikan garis tidak saling memotong.')
    if not WILAYAH.contains(geom):
        raise GeometryError('Polygon berada di luar wilayah operasi Berau.')
    luas = luas_ha(geom)
    if luas > LUAS_MAKS_HA:
        raise GeometryError(f'Luas {luas:,.1f} ha terlalu besar (maks. {LUAS_MAKS_HA} ha).')

    summary = f'luas {luas:,.2f} ha'
    if cctv.posisi:
        spec = getattr(cctv, 'spec', None)
        radius = spec.radius_m if spec and spec.radius_m else None
        batas = radius * JARAK_FAKTOR if radius else JARAK_DEFAULT_M
        local = geom.transform(UTM_50N, clone=True)
        camera = cctv.posisi.transform(UTM_50N, clone=True)
        jarak = max(Point(x, y, srid=UTM_50N).distance(camera) for x, y in local.convex_hull.coords[0])
        if jarak > batas:
            raise GeometryError(
                f'Titik terjauh polygon {jarak:,.0f} m dari kamera, melebihi batas {batas:,.0f} m '
                f'({"radius " + format(radius, ",.0f") + " m × " + str(JARAK_FAKTOR) if radius else "default"}).'
            )
        summary += f', titik terjauh {jarak:,.0f} m dari kamera'
    return summary


def _check_geometry(jenis, geom, cctv):
    if jenis == PengajuanCoverage.Jenis.DICABUT:
        return None  # nothing to draw: the active coverage is withdrawn
    return validate_geometry(geom, cctv)


def _log(pengajuan, aksi, user=None, catatan=''):
    RiwayatPengajuan.objects.create(pengajuan=pengajuan, aksi=aksi, user=user, catatan=catatan)


@transaction.atomic
def submit(pengajuan, user, orthophoto=None):
    """Save a new request (unsaved instance with cctv, jenis, geom, ...) and send it to SGI."""
    summary = _check_geometry(pengajuan.jenis, pengajuan.geom, pengajuan.cctv)
    _, versi = active_coverage(pengajuan.cctv)
    pengajuan.versi = versi + 1
    pengajuan.status = Status.MENUNGGU_SGI
    pengajuan.diajukan_oleh = user
    pengajuan.orthophoto = orthophoto
    if pengajuan.geom:
        pengajuan.digitasi_oleh, pengajuan.digitasi_pada = user, timezone.now()
    pengajuan.save()
    _log(pengajuan, Aksi.DIAJUKAN, user, pengajuan.get_jenis_display().lower())
    if summary:
        _log(pengajuan, Aksi.DIGITASI, user, f'v{pengajuan.versi}')
        _log(pengajuan, Aksi.VALIDASI, None, summary)
    return pengajuan


@transaction.atomic
def resubmit(pengajuan, user, orthophoto=None):
    """Send a returned request back to SGI after it was revised; the checklists start over."""
    summary = _check_geometry(pengajuan.jenis, pengajuan.geom, pengajuan.cctv)
    pengajuan.status = Status.MENUNGGU_SGI
    pengajuan.checklist_sgi, pengajuan.checklist_safety = [], []
    pengajuan.orthophoto = orthophoto or pengajuan.orthophoto
    if pengajuan.geom:
        pengajuan.digitasi_oleh, pengajuan.digitasi_pada = user, timezone.now()
    pengajuan.save()
    if summary:
        _log(pengajuan, Aksi.DIGITASI, user, f'v{pengajuan.versi} (revisi)')
        _log(pengajuan, Aksi.VALIDASI, None, summary)
    return pengajuan


GCP_MIN, GCP_MAX = 4, 12
IMAGE_MAX_PX = 10_000


def clean_kalibrasi(data):
    """Validate a camera calibration posted by the GeoPoint panel; returns the cleaned dict.

    Pixels must lie inside the screenshot and positions inside the operating area.
    The homography itself is solved in the browser (static/js/homography.js).
    """
    try:
        lebar, tinggi = int(data['lebar']), int(data['tinggi'])
        points = [
            {'px': float(g['px']), 'py': float(g['py']), 'lon': float(g['lon']), 'lat': float(g['lat'])}
            for g in data['gcp']
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise GeometryError('Data kalibrasi tidak lengkap.') from exc
    if not (0 < lebar <= IMAGE_MAX_PX and 0 < tinggi <= IMAGE_MAX_PX):
        raise GeometryError('Ukuran gambar tidak valid.')
    if not GCP_MIN <= len(points) <= GCP_MAX:
        raise GeometryError(f'Butuh {GCP_MIN}–{GCP_MAX} titik kontrol (GCP).')
    for i, g in enumerate(points, start=1):
        if not (0 <= g['px'] <= lebar and 0 <= g['py'] <= tinggi):
            raise GeometryError(f'GCP {i} di luar gambar.')
        if not WILAYAH.contains(Point(g['lon'], g['lat'], srid=4326)):
            raise GeometryError(f'GCP {i} di luar wilayah operasi Berau.')
    return {'gcp': points, 'lebar': lebar, 'tinggi': tinggi}


def jangkauan_m(cctv):
    """How far the camera is expected to see: its radius x JARAK_FAKTOR, or the default."""
    spec = getattr(cctv, 'spec', None)
    return spec.radius_m * JARAK_FAKTOR if spec and spec.radius_m else JARAK_DEFAULT_M


def active_kalibrasi(cctv):
    return KalibrasiKamera.objects.filter(cctv=cctv, aktif=True).first()


def _camera_xy(cctv, gcps):
    if cctv.posisi:
        return homography.to_utm(*cctv.posisi.coords)
    xs, ys = zip(*(homography.to_utm(g['lon'], g['lat']) for g in gcps))
    return sum(xs) / len(xs), sum(ys) / len(ys)


@transaction.atomic
def save_kalibrasi(pengajuan, user, data):
    """Safety's GCPs for this camera, solved with OpenCV and kept as the camera's calibration.

    The previous calibration of the camera is switched off. Then the request is analysed.
    Raises GeometryError with a message for the user.
    """
    if not pengajuan.tangkapan_layar:
        raise GeometryError('Upload tangkapan layar CCTV dulu.')
    cleaned = clean_kalibrasi(data)
    try:
        cal = homography.calibrate(cleaned['gcp'], cleaned['lebar'], cleaned['tinggi'])
    except homography.CalibrationError as exc:
        raise GeometryError(str(exc)) from exc
    KalibrasiKamera.objects.filter(cctv=pengajuan.cctv, aktif=True).update(aktif=False)
    with pengajuan.tangkapan_layar.open('rb') as screenshot:
        content = ContentFile(screenshot.read())
    kalibrasi = KalibrasiKamera(cctv=pengajuan.cctv, gcp=cleaned['gcp'], lebar=cleaned['lebar'],
                                tinggi=cleaned['tinggi'], hasil=cal.as_json(), dibuat_oleh=user)
    kalibrasi.gambar.save(f'{pengajuan.cctv.no_cctv}.jpg', content, save=False)
    kalibrasi.save()
    outliers = [i + 1 for i, ok in enumerate(cal.inliers) if not ok]
    _log(pengajuan, Aksi.KALIBRASI, user,
         f'{len(cleaned["gcp"])} GCP, RMSE {cal.rmse_m:.2f} m'
         + (f', GCP {", ".join(map(str, outliers))} diabaikan (outlier)' if outliers else ''))
    try:
        analyse_pengajuan(pengajuan, kalibrasi)
    except homography.CalibrationError as exc:  # e.g. an unreadable screenshot: keep nothing
        raise GeometryError(str(exc)) from exc
    return kalibrasi


def _geom_key(pengajuan, kalibrasi):
    return f'{kalibrasi.pk}:{hashlib.md5(bytes(pengajuan.geom.wkb)).hexdigest()}' if pengajuan.geom else ''


def analysis_is_stale(pengajuan, kalibrasi):
    return bool(kalibrasi and pengajuan.geom and pengajuan.tangkapan_layar
                and pengajuan.analisis.get('kunci') != _geom_key(pengajuan, kalibrasi))


def analyse_pengajuan(pengajuan, kalibrasi):
    """Run the homography check and draw the evidence image; stored on the request."""
    if not (kalibrasi and pengajuan.geom and pengajuan.tangkapan_layar):
        return None
    cal = homography.from_json(kalibrasi.hasil)
    result, visible, view = homography.analyse(cal, pengajuan.geom, _camera_xy(pengajuan.cctv, kalibrasi.gcp),
                                               jangkauan_m(pengajuan.cctv))
    summary = (f"{pengajuan.cctv.no_cctv}  terlihat {result['persen_terlihat']:.0f}%  "
               f"terjauh {result['titik_terjauh_m']:.0f} m / {result['jangkauan_m']:.0f} m  "
               f"RMSE {result['rmse_m']:.1f} m")
    with pengajuan.tangkapan_layar.open('rb') as screenshot:
        jpeg = homography.render_evidence(screenshot.read(), cal, visible, view, kalibrasi.gcp, summary)
    pengajuan.analisis = {
        **result,
        'area_pandang': homography.geometry_to_geojson(view),
        'kalibrasi_id': kalibrasi.pk,
        'kunci': _geom_key(pengajuan, kalibrasi),
        'dihitung': timezone.now().isoformat(),
    }
    if pengajuan.bukti:
        pengajuan.bukti.delete(save=False)
    pengajuan.bukti.save(f'bukti-{pengajuan.cctv.no_cctv}.jpg', ContentFile(jpeg), save=False)
    pengajuan.save(update_fields=['analisis', 'bukti', 'diubah'])
    return pengajuan.analisis


def open_request(cctv):
    return PengajuanCoverage.objects.filter(cctv=cctv).exclude(status=Status.SELESAI).first()


def create_digitasi_task(cctv, user, jenis, catatan=''):
    """A digitisation task for SGI after IT added (jenis baru) or moved (dipindah) a CCTV.

    Returns the task, or the already open request for this CCTV (one open request per CCTV),
    with the new change noted in its history.
    """
    if jenis == PengajuanCoverage.Jenis.DIPINDAH:
        # A moved camera sees a different patch of ground: its calibration no longer applies.
        KalibrasiKamera.objects.filter(cctv=cctv, aktif=True).update(aktif=False)
    existing = open_request(cctv)
    if existing:
        _log(existing, Aksi.DIAJUKAN, user, f'perubahan lagi: {catatan}' if catatan else 'perubahan lagi')
        return existing
    from orthophoto.models import Orthophoto
    _, versi = active_coverage(cctv)
    task = PengajuanCoverage.objects.create(
        cctv=cctv, jenis=jenis, status=Status.MENUNGGU_DIGITASI, versi=versi + 1, diajukan_oleh=user,
        orthophoto=Orthophoto.objects.filter(aktif=True).first(),
    )
    _log(task, Aksi.DIAJUKAN, user, catatan or task.get_jenis_display().lower())
    return task


@transaction.atomic
def save_digitasi(pengajuan, user, geom, *, kirim, catatan='', orthophoto=None):
    """SGI's polygon for a task: kept as a draft, or (kirim) checked and sent to Safety."""
    if pengajuan.status != Status.MENUNGGU_DIGITASI:
        raise ReviewError('Tugas ini tidak sedang menunggu digitasi.')
    pengajuan.geom = geom
    pengajuan.digitasi_oleh = user
    pengajuan.orthophoto = orthophoto or pengajuan.orthophoto
    if not kirim:
        pengajuan.save()
        return pengajuan
    summary = validate_geometry(geom, pengajuan.cctv)  # raises GeometryError
    pengajuan.status = Status.MENUNGGU_SAFETY
    pengajuan.digitasi_pada = timezone.now()
    pengajuan.checklist_safety = []
    pengajuan.save()
    _log(pengajuan, Aksi.DIGITASI, user, f'v{pengajuan.versi}' + (f' · {catatan}' if catatan else ''))
    _log(pengajuan, Aksi.VALIDASI, None, summary)
    return pengajuan


@dataclass
class ReviewError(Exception):
    message: str


def _checklist_keys(tahap):
    items = PengajuanCoverage.CHECKLIST_SGI if tahap == 'sgi' else PengajuanCoverage.CHECKLIST_SAFETY
    return [key for key, _ in items]


def _this_monday():
    today = timezone.localdate()
    return today - datetime.timedelta(days=today.weekday())


@transaction.atomic
def review(pengajuan, user, *, setujui, checklist, catatan=''):
    """Approve or return the request at its current stage (SGI or Safety)."""
    tahap = pengajuan.tahap
    if not tahap:
        raise ReviewError('Pengajuan ini tidak sedang menunggu review.')
    keys = _checklist_keys(tahap)
    checklist = [key for key in keys if key in set(checklist)]
    catatan = catatan.strip()
    setattr(pengajuan, f'checklist_{tahap}', checklist)

    if not setujui:
        if not catatan:
            raise ReviewError('Catatan review wajib diisi jika dikembalikan.')
        # SGI sends a request back to IT; Safety sends the polygon back to SGI to redraw.
        pengajuan.status = Status.DIKEMBALIKAN if tahap == 'sgi' else Status.MENUNGGU_DIGITASI
        if tahap == 'safety':
            pengajuan.digitasi_pada = None
        pengajuan.save()
        _log(pengajuan, Aksi.DIKEMBALIKAN, user, f'{tahap.upper()}: {catatan}')
        return pengajuan

    if checklist != keys:
        raise ReviewError('Centang semua poin checklist sebelum menyetujui.')
    if tahap == 'sgi':
        pengajuan.status = Status.MENUNGGU_SAFETY
        pengajuan.save()
        _log(pengajuan, Aksi.DISETUJUI_SGI, user, catatan)
        return pengajuan

    pengajuan.status = Status.SELESAI
    pengajuan.save()
    CctvCoverage.objects.update_or_create(
        cctv=pengajuan.cctv, minggu=_this_monday(), defaults={'geom': pengajuan.geom},
    )
    _log(pengajuan, Aksi.DISETUJUI_SAFETY, user, catatan or f'coverage v{pengajuan.versi} aktif')
    return pengajuan
