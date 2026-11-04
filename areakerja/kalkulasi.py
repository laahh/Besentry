"""CCTV coverage of work areas, per Mitra Site x mode x kind.

    tercover        union(boundary of active CCTV) ∩ area
    potensi         (union(dead CCTV) − union(active CCTV)) ∩ area      covered once they are switched on
    tidak tercover  area − union(every counted CCTV)

Boundaries are dissolved first, so ground seen by two cameras counts once. Done in UTM 50N.

    hitung_live()            the running week, kept in CoverageLive (after every change, see apps.py)
    tutup_minggu(minggu)     Monday 00:00: lock last week per Mitra Site, against that week's CCTV snapshot
    selesaikan_menunggu()    weeks waiting for a late approval: computed once Approved, or with the
                             fallback area when batas_tunggu passes
"""

import datetime
import logging
from dataclasses import dataclass

import django_rq
from django.contrib.gis.geos import GEOSGeometry
from django.db import transaction
from django.utils import timezone

from cctv.coverage import UTM_50N, this_monday
from cctv.models import Cctv, SnapshotMingguan
from cctv.snapshot import boundaries

from . import services
from .models import (
    CoverageLive, JenisArea, MitraSite, ModeKalkulasi, PengajuanAreaKerja, SnapshotCoverage, SnapshotCoverageJenis,
)

logger = logging.getLogger(__name__)
QUEUE = 'coverage'
JENIS = [jenis.value for jenis in JenisArea]
TUNGGU_HARI = 7  # a late week waits at most one more week after it closes

# Status / kondisi of a CCTV -> how it counts. Dismantled or taken out: not at all.
AKTIF_STATUS = ('live', 'post_event')
MATI_KONDISI = ('breakdown', 'repair')
DICABUT_KONDISI = ('dismantle', 'take_out')


def kelompok(status, kondisi):
    """'aktif', 'mati' or None (not counted)."""
    if kondisi in DICABUT_KONDISI:
        return None
    if status == 'offline' or kondisi in MATI_KONDISI:
        return 'mati'
    if status in AKTIF_STATUS:
        return 'aktif'
    return 'mati'  # no status recorded: not proven live, so only potential


@dataclass(frozen=True)
class Kamera:
    site_id: int
    perusahaan_id: int
    kelompok: str
    geom: GEOSGeometry  # UTM 50N


def _kamera(rows):
    """[(site_id, perusahaan_id, status, kondisi, boundary 4326)] -> counted cameras with a boundary."""
    result = []
    for site_id, perusahaan_id, status, kondisi, geom in rows:
        group = kelompok(status, kondisi)
        if group and geom is not None and not geom.empty:
            result.append(Kamera(site_id, perusahaan_id, group, geom.transform(UTM_50N, clone=True)))
    return result


def kamera_live():
    boundary_of = boundaries()
    rows = Cctv.objects.filter(pk__in=list(boundary_of)).values_list('pk', 'site_id', 'perusahaan_id', 'status', 'kondisi')
    return _kamera((s, p, st, k, boundary_of[pk]) for pk, s, p, st, k in rows)


def kamera_snapshot(minggu):
    rows = SnapshotMingguan.objects.filter(minggu=minggu, coverage__isnull=False).values_list('data', 'coverage')
    return _kamera((d.get('site_id'), d.get('perusahaan_id'), d.get('status'), d.get('kondisi'), g) for d, g in rows)


def _union(geoms):
    geoms = list(geoms)
    if not geoms:
        return None
    union = geoms[0]
    for geom in geoms[1:]:
        union = union.union(geom)
    return union


def _back(geom):
    """UTM result -> MultiPolygon in EPSG:4326, or None."""
    if geom is None or geom.empty:
        return None
    geom = geom.transform(4326, clone=True)
    geom.srid = 4326
    return services._multi(geom)


def _ha(geom):
    return round(geom.area / 10_000, 4) if geom is not None and not geom.empty else 0.0


def hitung(mitra_site, areas, kamera):
    """{jenis: MultiPolygon 4326 or None} x cameras -> [dict of HasilCoverage fields] for both modes x 4 kinds."""
    local = {jenis: (areas.get(jenis).transform(UTM_50N, clone=True) if areas.get(jenis) else None) for jenis in JENIS}
    present = [geom for geom in local.values() if geom is not None]
    extent = _union(present).envelope if present else None
    nearby = [k for k in kamera if extent is not None and k.geom.intersects(extent)]
    modes = {
        ModeKalkulasi.MILIK: [k for k in nearby if (k.site_id, k.perusahaan_id) == (mitra_site.site_id, mitra_site.perusahaan_id)],
        ModeKalkulasi.SEMUA: nearby,
    }
    results = []
    for mode, cams in modes.items():
        aktif = _union(k.geom for k in cams if k.kelompok == 'aktif')
        mati = _union(k.geom for k in cams if k.kelompok == 'mati')
        semua = _union([g for g in (aktif, mati) if g is not None])
        for jenis in JENIS:
            area = local[jenis]
            row = {'mode': mode, 'jenis': jenis, 'luas_ha': _ha(area), 'jumlah_cctv': 0,
                   'tercover_ha': 0.0, 'potensi_ha': 0.0, 'tidak_ha': _ha(area),
                   'geom_tercover': None, 'geom_potensi': None, 'geom_tidak': _back(area)}
            if area is not None:
                tercover = area.intersection(aktif) if aktif is not None else None
                potensi = area.intersection(mati.difference(aktif) if aktif is not None else mati) if mati is not None else None
                tidak = area.difference(semua) if semua is not None else area
                row.update(
                    tercover_ha=_ha(tercover), potensi_ha=_ha(potensi), tidak_ha=_ha(tidak),
                    geom_tercover=_back(tercover), geom_potensi=_back(potensi), geom_tidak=_back(tidak),
                    jumlah_cctv=sum(1 for k in cams if k.geom.intersects(area)),
                )
            results.append(row)
    return results


def areas_of(pengajuan):
    return {area.jenis: area.geom for area in pengajuan.area.all()} if pengajuan else {}


def pilihan_slot(pengajuan):
    if not pengajuan:
        return {}
    return {area.jenis: {'sumber': area.sumber, 'minggu_asal': area.minggu_asal.isoformat() if area.minggu_asal else None}
            for area in pengajuan.area.all()}


# --- Live ---------------------------------------------------------------------------------------

@transaction.atomic
def hitung_live(mitra_site_ids=None):
    """Recompute the running week. Returns the number of Mitra Sites done."""
    minggu = this_monday()
    sites = MitraSite.objects.aktif()
    if mitra_site_ids is not None:
        sites = sites.filter(pk__in=mitra_site_ids)
    CoverageLive.objects.exclude(mitra_site__aktif=True).delete()
    kamera = kamera_live()
    done = 0
    for mitra_site in sites:
        berlaku = services.area_berlaku(mitra_site, minggu)
        rows = hitung(mitra_site, areas_of(berlaku['pengajuan']), kamera)
        CoverageLive.objects.filter(mitra_site=mitra_site).delete()
        CoverageLive.objects.bulk_create([
            CoverageLive(mitra_site=mitra_site, minggu=minggu, minggu_area=berlaku['minggu_area'],
                         fallback=berlaku['fallback'], catatan=berlaku['catatan'][:200], **row) for row in rows
        ])
        done += 1
    return done


def enqueue_live():
    try:
        # commit_mode='auto': this already runs after the commit (see jadwalkan_live).
        django_rq.get_queue(QUEUE, commit_mode='auto').enqueue(hitung_live)
    except Exception:  # Redis down: the next change, or the Monday close, catches up
        logger.exception('Antrean coverage tidak bisa dipakai; coverage live belum dihitung ulang.')


def jadwalkan_live():
    """Recompute once after the current transaction, however many rows it changed.

    Looks in the transaction's own on-commit list rather than keeping a flag: a rolled-back
    transaction drops that list, so nothing is left behind to block the next recompute.
    """
    connection = transaction.get_connection()
    if any(getattr(entry[1], 'coverage_live', False) for entry in connection.run_on_commit):
        return
    transaction.on_commit(enqueue_live)


enqueue_live.coverage_live = True


# --- Weekly snapshot ----------------------------------------------------------------------------

def _lock(snapshot, pengajuan, kamera, catatan):
    rows = hitung(snapshot.mitra_site, areas_of(pengajuan), kamera)
    SnapshotCoverageJenis.objects.filter(snapshot=snapshot).delete()
    SnapshotCoverageJenis.objects.bulk_create([SnapshotCoverageJenis(snapshot=snapshot, **row) for row in rows])
    snapshot.status = SnapshotCoverage.Status.TERKUNCI
    snapshot.pengajuan = pengajuan
    snapshot.minggu_area = pengajuan.minggu if pengajuan else None
    snapshot.fallback = bool(pengajuan) and pengajuan.minggu != snapshot.minggu
    snapshot.catatan = catatan[:200]
    snapshot.pilihan_slot = pilihan_slot(pengajuan)
    snapshot.terkunci_pada = timezone.now()
    snapshot.save()
    return snapshot


def _menunggu(mitra_site, minggu):
    """The week's own request is still on its way (not Approved yet)."""
    return PengajuanAreaKerja.objects.filter(mitra_site=mitra_site, minggu=minggu).exclude(
        status=PengajuanAreaKerja.Status.APPROVED).exists()


@transaction.atomic
def tutup_minggu(minggu):
    """Close week `minggu` (a Monday) for every active Mitra Site, against that week's CCTV snapshot
    (cctv.snapshot.close_week runs first). Returns (locked, waiting). Weeks already closed are left
    alone; a week that has not ended yet is not closed."""
    if minggu + datetime.timedelta(days=7) > this_monday():
        return 0, 0
    kamera = kamera_snapshot(minggu)
    done = SnapshotCoverage.objects.filter(minggu=minggu).values('mitra_site')
    locked = waiting = 0
    for mitra_site in MitraSite.objects.aktif().exclude(pk__in=done).select_related('site', 'perusahaan'):
        snapshot = SnapshotCoverage(minggu=minggu, mitra_site=mitra_site, site_kode=mitra_site.site.kode,
                                    perusahaan_nama=mitra_site.perusahaan.nama)
        berlaku = services.area_berlaku(mitra_site, minggu)
        if berlaku['fallback'] and _menunggu(mitra_site, minggu):
            snapshot.status = SnapshotCoverage.Status.MENUNGGU
            snapshot.batas_tunggu = minggu + datetime.timedelta(days=7 + TUNGGU_HARI)
            snapshot.catatan = 'Menunggu area kerja week ini Approved'
            snapshot.save()
            waiting += 1
            continue
        snapshot.save()
        _lock(snapshot, berlaku['pengajuan'], kamera, berlaku['catatan'])
        locked += 1
    return locked, waiting


@transaction.atomic
def selesaikan_menunggu(mitra_site=None, today=None):
    """Lock waiting weeks whose request is now Approved, or whose wait is over. Returns how many."""
    today = today or timezone.localdate()
    waiting = SnapshotCoverage.objects.select_for_update(of=('self',)).filter(status=SnapshotCoverage.Status.MENUNGGU)
    if mitra_site is not None:
        waiting = waiting.filter(mitra_site=mitra_site)
    done = 0
    for snapshot in waiting.select_related('mitra_site'):
        berlaku = services.area_berlaku(snapshot.mitra_site, snapshot.minggu) if snapshot.mitra_site else None
        approved = berlaku and not berlaku['fallback'] and berlaku['pengajuan']
        if not approved and today < snapshot.batas_tunggu:
            continue
        kamera = kamera_snapshot(snapshot.minggu)  # the CCTV as they stood when the week closed
        if approved:
            _lock(snapshot, berlaku['pengajuan'], kamera, 'Approved terlambat')
        else:
            catatan = f"Fallback, area week ini tidak tersedia ({berlaku['catatan']})" if berlaku else 'Fallback'
            _lock(snapshot, berlaku['pengajuan'] if berlaku else None, kamera, catatan)
        done += 1
    return done


def ringkasan(rows, mode=ModeKalkulasi.MILIK):
    """Totals over the kinds for one mode: {luas, tercover, potensi, tidak} in ha and percent."""
    rows = [r for r in rows if r.mode == mode]
    luas = sum(r.luas_ha for r in rows)
    total = {key: sum(getattr(r, f'{key}_ha') for r in rows) for key in ('tercover', 'potensi', 'tidak')}
    # Percentages from the exact sums: rounding the hectares first can push a small area past 100%.
    persen = {key: (min(100.0, round(100 * value / luas, 1)) if luas else None) for key, value in total.items()}
    return {'luas': round(luas, 4), **{key: round(value, 4) for key, value in total.items()}, 'persen': persen}
