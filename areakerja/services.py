"""Weekly work areas: uploader -> Verifikasi BC (SGI, Mining, Safety BC) -> Approved, and which area
counts for a week. The uploader is the Mitra, or Survey BC for PT Berau Coal's own areas.

    request_for(mitra_site)      this week's request (created as a Draft when there is none)
    save_slot()                  one kind: upload a zip SHP, reuse last week's area, or none
    ajukan()                     -> Waiting Verifikasi BC; earlier approvals are cleared
    verifikasi()                 one department: Disetujui (Approved once all three have), or back to
                                 the uploader with a note
    tambah_anotasi() ...         reviewers' points with notes on the map; the uploader ticks them solved
    area_berlaku(mitra_site, w)  the area that counts for week w: that week's Approved request,
                                 else the last Approved one before it (a fallback, with a note)

Every step sends `area_kerja_berubah` (see signals.py); notifikasi.area_kerja tells people about it.
"""

import datetime
import json

from django.contrib.gis.geos import GEOSException, GEOSGeometry, LineString, MultiPolygon, Point, Polygon
from django.db import transaction
from django.utils import timezone

from cctv.coverage import GeometryError, ReviewError, UTM_50N, _batas_site, read_shp_zip, this_monday
from cctv.models import minggu_label

from .models import (
    AnotasiAreaKerja, AreaKerja, Departemen, JenisArea, MitraSite, PengajuanAreaKerja, RiwayatAreaKerja,
    VerifikasiAreaKerja,
)
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


def _signal(pengajuan, user, aksi, **extra):
    area_kerja_berubah.send(sender=PengajuanAreaKerja, pengajuan=pengajuan, user=user, aksi=aksi, **extra)


def request_for(mitra_site, user):
    """This week's request for a Mitra Site; only the running week can be filled in."""
    if not mitra_site.aktif:
        raise ReviewError('Operasional Sudah Berhenti: Mitra Site ini nonaktif.')
    pengajuan, _ = PengajuanAreaKerja.objects.get_or_create(
        mitra_site=mitra_site, minggu=this_monday(), defaults={'diajukan_oleh': user},
    )
    return pengajuan


def editable(pengajuan):
    """The uploader fills a Draft, or a request sent back. Once Approved nothing changes."""
    return pengajuan.status in (Status.DRAFT, Status.RETURN_MITRA)


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

    area, _ = AreaKerja.objects.update_or_create(pengajuan=pengajuan, jenis=jenis, defaults=values)
    pengajuan = PengajuanAreaKerja.objects.prefetch_related('area').get(pk=pengajuan.pk)
    clip_by_priority(pengajuan)
    area.refresh_from_db()
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
    pengajuan.catatan = catatan.strip()
    pengajuan.diajukan_oleh, pengajuan.diajukan_pada = user, timezone.now()
    VerifikasiAreaKerja.objects.filter(pengajuan=pengajuan).delete()  # a new upload is verified afresh
    pengajuan.status = Status.MENUNGGU_SGI
    pengajuan.save()
    _log(pengajuan, Aksi.DIAJUKAN, user, pengajuan.catatan)
    _signal(pengajuan, user, Aksi.DIAJUKAN)
    return pengajuan


@transaction.atomic
def verifikasi(pengajuan, user, *, departemen, aksi, catatan=''):
    """One department of Verifikasi BC: aksi 'setujui' (Disetujui; Approved once SGI, Mining and
    Safety BC all have) or 'kembalikan' (back to the uploader, a note is required)."""
    if pengajuan.status != Status.MENUNGGU_SGI:
        raise ReviewError(f'Pengajuan ini sedang {pengajuan.status_label}, bukan menunggu verifikasi.')
    if departemen not in Departemen.values:
        raise ReviewError('Departemen tidak dikenal.')
    label = Departemen(departemen).label
    catatan = catatan.strip()
    if aksi == 'kembalikan':
        if not catatan:
            raise ReviewError('Catatan wajib diisi jika dikembalikan.')
        pengajuan.status = Status.RETURN_MITRA
        pengajuan.save()
        _log(pengajuan, Aksi.DIKEMBALIKAN, user, f'{label}: {catatan}')
        _signal(pengajuan, user, Aksi.DIKEMBALIKAN)
        return pengajuan
    if aksi != 'setujui':
        raise ReviewError('Pilih tindakan verifikasi.')
    _, created = VerifikasiAreaKerja.objects.get_or_create(pengajuan=pengajuan, departemen=departemen,
                                                           defaults={'user': user})
    if not created:
        raise ReviewError(f'{label} sudah menyetujui pengajuan ini.')
    _log(pengajuan, Aksi.VERIFIKASI, user, label + (f': {catatan}' if catatan else ''))
    done = set(pengajuan.verifikasi.values_list('departemen', flat=True))
    if done >= set(Departemen.values):
        pengajuan.status = Status.APPROVED
        pengajuan.disetujui_oleh, pengajuan.disetujui_pada = user, timezone.now()
        pengajuan.save()
        _log(pengajuan, Aksi.DISETUJUI, None, 'SGI, Mining, dan Safety BC menyetujui')
        _signal(pengajuan, user, Aksi.DISETUJUI)
    else:
        _signal(pengajuan, user, Aksi.VERIFIKASI, departemen=label)
    return pengajuan


# --- Map notes ----------------------------------------------------------------------------------

ANOTASI_MAX_TITIK = 500  # vertices of a drawn line or area


def _bentuk_anotasi(geometri):
    """GeoJSON of a line or an area -> (bentuk, geometry, anchor point, length m, area ha)."""
    try:  # too few points do not even make a geometry: say so before GEOS refuses it
        raw = json.loads(geometri)
        if raw.get('type') == 'LineString' and len(raw.get('coordinates') or []) < 2:
            raise ReviewError('Garis anotasi perlu minimal 2 titik.')
    except (ValueError, AttributeError) as exc:
        raise ReviewError('Bentuk anotasi tidak terbaca.') from exc
    try:
        geom = GEOSGeometry(geometri, srid=4326)
    except (GEOSException, ValueError, TypeError) as exc:
        raise ReviewError('Bentuk anotasi tidak terbaca.') from exc
    if isinstance(geom, LineString):
        if geom.num_points < 2:
            raise ReviewError('Garis anotasi perlu minimal 2 titik.')
        bentuk, anchor = AnotasiAreaKerja.Bentuk.GARIS, geom.interpolate_normalized(0.5)
    elif isinstance(geom, Polygon):
        if geom.num_points < 4:  # a closed ring repeats its first vertex
            raise ReviewError('Area anotasi perlu minimal 3 titik.')
        if not geom.valid:
            raise ReviewError('Area anotasi tidak valid: garisnya saling memotong.')
        bentuk, anchor = AnotasiAreaKerja.Bentuk.AREA, geom.point_on_surface
    else:
        raise ReviewError('Anotasi harus berupa titik, garis, atau area.')
    if geom.num_points > ANOTASI_MAX_TITIK:
        raise ReviewError(f'Bentuk anotasi terlalu rinci (lebih dari {ANOTASI_MAX_TITIK} titik).')
    local = geom.transform(UTM_50N, clone=True)
    panjang = round(local.length, 1) if bentuk == AnotasiAreaKerja.Bentuk.GARIS else None
    luas = round(local.area / 10_000, 4) if bentuk == AnotasiAreaKerja.Bentuk.AREA else None
    return bentuk, geom, anchor, panjang, luas


def tambah_anotasi(pengajuan, user, departemen, teks, *, lon=None, lat=None, geometri=None):
    """A reviewer's note on the map while the request waits for Verifikasi BC: a point (lon, lat),
    or a line / an area (`geometri`, GeoJSON). Lines get their length, areas their size."""
    if pengajuan.status != Status.MENUNGGU_SGI:
        raise ReviewError('Anotasi hanya bisa ditambahkan saat Verifikasi BC.')
    teks = (teks or '').strip()
    if not teks:
        raise ReviewError('Tulis catatan anotasinya.')
    values = {'bentuk': AnotasiAreaKerja.Bentuk.TITIK}
    if geometri:
        bentuk, geom, posisi, panjang, luas = _bentuk_anotasi(geometri)
        values.update(bentuk=bentuk, geom=geom, panjang_m=panjang, luas_ha=luas)
    else:
        try:
            posisi = Point(float(lon), float(lat), srid=4326)
        except (TypeError, ValueError) as exc:
            raise ReviewError('Titik anotasi tidak valid.') from exc
    batas, nama_batas = _batas_site()
    if not batas.contains(values.get('geom') or posisi):
        raise ReviewError(f'Anotasi berada di luar batas ({nama_batas}).')
    return AnotasiAreaKerja.objects.create(pengajuan=pengajuan, departemen=departemen, user=user,
                                           posisi=posisi, teks=teks[:1000], **values)


def hapus_anotasi(anotasi, user):
    """Its author takes it back, while Verifikasi BC is still going on."""
    if anotasi.user_id != user.pk or anotasi.pengajuan.status != Status.MENUNGGU_SGI:
        raise ReviewError('Anotasi ini tidak bisa dihapus.')
    anotasi.delete()


def tandai_anotasi(anotasi, user, selesai):
    """The uploader ticks a note solved (or not) while fixing the request."""
    if not editable(anotasi.pengajuan):
        raise ReviewError('Anotasi ditandai selesai saat memperbaiki pengajuan.')
    anotasi.selesai_oleh, anotasi.selesai_pada = (user, timezone.now()) if selesai else (None, None)
    anotasi.save(update_fields=['selesai_oleh', 'selesai_pada'])
    return anotasi


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
