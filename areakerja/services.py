"""Weekly work areas: Mitra -> Survey BC -> Approved, and which area counts for a week.

    request_for(mitra_site)      this week's request (created as a Draft when there is none)
    save_slot()                  one kind: upload a zip SHP, reuse last week's area, or none
    ajukan()                     Mitra: -> Waiting Survey BC. PT Berau Coal (Survey BC): Approved at once
    verifikasi()                 Survey BC: Approved, or Return to Mitra with a note
    area_berlaku(mitra_site, w)  the area that counts for week w: that week's Approved request,
                                 else the last Approved one before it (a fallback, with a note)

Every step sends `area_kerja_berubah` (see signals.py) so notifications can hook in.
"""

import datetime

from django.contrib.gis.geos import GEOSException, MultiPolygon, Polygon
from django.db import transaction
from django.utils import timezone

from cctv.coverage import GeometryError, ReviewError, UTM_50N, _batas_site, read_shp_zip, this_monday
from cctv.models import minggu_label

from .models import AreaKerja, JenisArea, MitraSite, PengajuanAreaKerja, RiwayatAreaKerja
from .signals import area_kerja_berubah

Status = PengajuanAreaKerja.Status
Sumber = AreaKerja.Sumber
Aksi = RiwayatAreaKerja.Aksi
JENIS = [jenis.value for jenis in JenisArea]  # priority order


def luas_ha(geom):
    return round(geom.transform(UTM_50N, clone=True).area / 10_000, 4) if geom and not geom.empty else 0.0


def _multi(geom):
    """Any polygonal result of an overlay -> MultiPolygon (or None when nothing is left)."""
    if geom is None or geom.empty:
        return None
    if isinstance(geom, Polygon):
        return MultiPolygon(geom, srid=4326)
    if isinstance(geom, MultiPolygon):
        return geom
    parts = [g for g in geom if isinstance(g, (Polygon, MultiPolygon)) and not g.empty]  # collection
    polygons = [p for g in parts for p in ([g] if isinstance(g, Polygon) else list(g))]
    return MultiPolygon(polygons, srid=4326) if polygons else None


# --- Reading and checking ----------------------------------------------------------------------

def read_area(upload):
    """A zipped SHP of one kind of work area -> (dissolved MultiPolygon, feature count). Rejects
    invalid polygons and anything outside the active orthophoto's extent."""
    geom, count = read_shp_zip(upload, satu_feature=False)
    for number, polygon in enumerate(geom, start=1):
        if not polygon.valid:
            raise GeometryError(f'Polygon {number} tidak valid ({polygon.valid_reason}).')
    try:
        dissolved = _multi(geom.unary_union)
    except GEOSException as exc:
        raise GeometryError('Polygon tidak bisa digabung (dissolve).') from exc
    batas, nama_batas = _batas_site()
    if not batas.contains(dissolved):
        raise GeometryError(f'Area kerja berada di luar batas ({nama_batas}). Upload ditolak.')
    return dissolved, count


def clip_by_priority(pengajuan):
    """Each kind loses what a higher kind already covers (Pit > Hauling > Infra Tambang > Infra Non Tambang)."""
    taken = None
    for jenis in JENIS:
        area = pengajuan.slot(jenis)
        if area is None:
            continue
        source = area.geom_upload
        if source is None:
            area.geom, area.luas_ha, area.dipotong_ha = None, 0.0, 0.0
        else:
            clipped = _multi(source.difference(taken)) if taken is not None else source
            area.geom = clipped
            area.luas_ha = luas_ha(clipped)
            area.dipotong_ha = round(max(0.0, luas_ha(source) - area.luas_ha), 4)
            taken = source if taken is None else taken.union(source)
        area.save(update_fields=['geom', 'luas_ha', 'dipotong_ha', 'diubah'])


# --- The request -------------------------------------------------------------------------------

def _log(pengajuan, aksi, user=None, catatan='', sebelum=None):
    RiwayatAreaKerja.objects.create(pengajuan=pengajuan, aksi=aksi, user=user, catatan=catatan, sebelum=sebelum or {})


def _signal(pengajuan, user, aksi):
    area_kerja_berubah.send(sender=PengajuanAreaKerja, pengajuan=pengajuan, user=user, aksi=aksi)


def request_for(mitra_site, user):
    """This week's request for a Mitra Site; only the running week can be filled in."""
    if not mitra_site.aktif:
        raise ReviewError('Operasional Sudah Berhenti: Mitra Site ini nonaktif.')
    pengajuan, _ = PengajuanAreaKerja.objects.get_or_create(
        mitra_site=mitra_site, minggu=this_monday(), defaults={'diajukan_oleh': user},
    )
    return pengajuan


def editable(pengajuan):
    """Mitra fills Draft / Return to Mitra; PT Berau Coal's own areas stay editable all their week."""
    if pengajuan.status in (Status.DRAFT, Status.RETURN_MITRA):
        return True
    return pengajuan.gabung and pengajuan.status == Status.APPROVED and pengajuan.minggu == this_monday()


@transaction.atomic
def save_slot(pengajuan, user, jenis, sumber, upload=None):
    """Fill one kind of area. Returns the AreaKerja row."""
    if not editable(pengajuan):
        raise ReviewError(f'Pengajuan ini sedang {pengajuan.get_status_display()}, tidak bisa diubah.')
    if jenis not in JENIS:
        raise ReviewError('Jenis area tidak dikenal.')
    values = {'sumber': sumber, 'file_shp': '', 'jumlah_feature': 0, 'minggu_asal': None, 'geom_upload': None}
    if sumber == Sumber.UPLOAD:
        if not upload:
            raise GeometryError('Pilih file zip SHP.')
        geom, count = read_area(upload)
        values.update(file_shp=upload.name[:255], jumlah_feature=count, geom_upload=geom)
    elif sumber == Sumber.MINGGU_LALU:
        lalu = area_berlaku(pengajuan.mitra_site, pengajuan.minggu - datetime.timedelta(days=7))
        source = lalu['pengajuan'].slot(jenis) if lalu['pengajuan'] else None
        if source is None or source.geom_upload is None:
            raise GeometryError('Belum ada area minggu lalu untuk jenis ini.')
        values.update(minggu_asal=lalu['minggu_area'], geom_upload=source.geom_upload,
                      jumlah_feature=source.jumlah_feature, file_shp=source.file_shp)
    elif sumber != Sumber.TIDAK_ADA:
        raise ReviewError('Pilih sumber area.')

    existing = pengajuan.slot(jenis)
    if existing and pengajuan.status == Status.APPROVED:  # PT Berau Coal editing an area in force
        _log(pengajuan, Aksi.DIUBAH, user, JenisArea(jenis).label, sebelum={
            'jenis': jenis, 'sumber': existing.sumber, 'luas_ha': existing.luas_ha,
            'geom': existing.geom_upload.geojson if existing.geom_upload else None,
        })
    area, _ = AreaKerja.objects.update_or_create(pengajuan=pengajuan, jenis=jenis, defaults=values)
    pengajuan = PengajuanAreaKerja.objects.prefetch_related('area').get(pk=pengajuan.pk)
    clip_by_priority(pengajuan)
    area.refresh_from_db()
    if pengajuan.status == Status.APPROVED:
        _signal(pengajuan, user, Aksi.DIUBAH)
    return area


def _complete(pengajuan):
    missing = [JenisArea(j).label for j in JENIS if pengajuan.slot(j) is None]
    if missing:
        raise ReviewError(f'Isi semua jenis area dulu: {", ".join(missing)} belum diisi.')


@transaction.atomic
def ajukan(pengajuan, user, catatan=''):
    if pengajuan.status not in (Status.DRAFT, Status.RETURN_MITRA):
        raise ReviewError(f'Pengajuan ini sedang {pengajuan.get_status_display()}.')
    _complete(pengajuan)
    now = timezone.now()
    pengajuan.catatan = catatan.strip()
    pengajuan.diajukan_oleh, pengajuan.diajukan_pada = user, now
    if pengajuan.gabung:  # Survey BC uploads PT Berau Coal's own areas and validates them itself
        pengajuan.status = Status.APPROVED
        pengajuan.disetujui_oleh, pengajuan.disetujui_pada = user, now
        pengajuan.save()
        _log(pengajuan, Aksi.DISETUJUI, user, 'Area PT Berau Coal, divalidasi Survey BC')
        _signal(pengajuan, user, Aksi.DISETUJUI)
        return pengajuan
    pengajuan.status = Status.MENUNGGU_SGI
    pengajuan.save()
    _log(pengajuan, Aksi.DIAJUKAN, user, pengajuan.catatan)
    _signal(pengajuan, user, Aksi.DIAJUKAN)
    return pengajuan


@transaction.atomic
def verifikasi(pengajuan, user, *, aksi, catatan=''):
    """Survey BC: aksi 'approve' or 'return' (a note is required)."""
    if pengajuan.status != Status.MENUNGGU_SGI:
        raise ReviewError(f'Pengajuan ini sedang {pengajuan.get_status_display()}, bukan menunggu verifikasi.')
    catatan = catatan.strip()
    if aksi == 'return':
        if not catatan:
            raise ReviewError('Catatan wajib diisi jika dikembalikan.')
        pengajuan.status = Status.RETURN_MITRA
        pengajuan.save()
        _log(pengajuan, Aksi.DIKEMBALIKAN, user, catatan)
        _signal(pengajuan, user, Aksi.DIKEMBALIKAN)
        return pengajuan
    if aksi != 'approve':
        raise ReviewError('Pilih tindakan verifikasi.')
    pengajuan.status = Status.APPROVED
    pengajuan.disetujui_oleh, pengajuan.disetujui_pada = user, timezone.now()
    pengajuan.save()
    _log(pengajuan, Aksi.DISETUJUI, user, catatan)
    _signal(pengajuan, user, Aksi.DISETUJUI)
    return pengajuan


# --- Which area counts --------------------------------------------------------------------------

def area_berlaku(mitra_site, minggu):
    """{pengajuan, minggu_area, fallback, catatan, berhenti} for week `minggu` (a Monday)."""
    if not mitra_site.aktif:
        return {'pengajuan': None, 'minggu_area': None, 'fallback': False, 'berhenti': True,
                'catatan': 'Operasional Sudah Berhenti'}
    approved = (PengajuanAreaKerja.objects.filter(mitra_site=mitra_site, status=Status.APPROVED, minggu__lte=minggu)
                .prefetch_related('area').order_by('-minggu').first())
    if approved is None:
        return {'pengajuan': None, 'minggu_area': None, 'fallback': False, 'berhenti': False,
                'catatan': 'Belum ada area kerja Approved'}
    if approved.minggu == minggu:
        catatan = 'Approved terlambat' if approved.terlambat else ''
        return {'pengajuan': approved, 'minggu_area': minggu, 'fallback': False, 'berhenti': False, 'catatan': catatan}
    weeks = (minggu - approved.minggu).days // 7
    return {'pengajuan': approved, 'minggu_area': approved.minggu, 'fallback': True, 'berhenti': False,
            'catatan': f'Memakai area {minggu_label(approved.minggu)} ({weeks} minggu)'}


def wajib_upload(minggu=None):
    """Active Mitra Sites without an Approved request for the week: who still has to upload."""
    minggu = minggu or this_monday()
    done = PengajuanAreaKerja.objects.filter(minggu=minggu, status=Status.APPROVED).values('mitra_site')
    return MitraSite.objects.aktif().exclude(pk__in=done).select_related('site', 'perusahaan')
