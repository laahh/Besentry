"""Data for the coverage dashboard: CCTV coverage of the work areas, per week.

The numbers are already computed (areakerja.kalkulasi); this module only reads them.

    week berjalan   CoverageLive, recomputed after every change
    week lalu       SnapshotCoverage (status terkunci) + its SnapshotCoverageJenis rows

Every function takes a Filter. Percentages always come from summed hectares (kalkulasi.ringkasan),
never from averaging percentages.
"""

import datetime
import json
from dataclasses import dataclass, replace

from django.contrib.gis.db.models import GeometryField
from django.contrib.gis.db.models.functions import AsGeoJSON, Transform
from django.db.models import F, FloatField, Func, Sum

from areakerja import kalkulasi, services
from areakerja.models import (
    CoverageLive, JENIS_AREA_UTAMA, JenisArea, MitraSite, ModeKalkulasi, SnapshotCoverage,
    SnapshotCoverageJenis,
)
from cctv.access import get_scope
from cctv.coverage import UTM_50N, this_monday
from cctv.models import Cctv, SnapshotMingguan, minggu_label
from core.models import Perusahaan, Site

LAPISAN = ('tercover', 'potensi', 'tidak')
TREN_MINGGU = 12
GARIS_WAKTU_MINGGU = 52  # the timeline shows a year even before every week has numbers
SIMPLIFY_M = 0.5  # metres; keeps the map light without visibly moving edges
JENIS_URUT = [value for value, _ in JenisArea.choices]
JENIS_UTAMA = [jenis.value for jenis in JENIS_AREA_UTAMA]
PTZ = (Cctv.Jenis.PTZ, Cctv.Jenis.PTZ_FIXED)


def can_open(user):
    """Survey BC, Mining BC, Safety BC and admins; not the contractors."""
    return get_scope(user).sees_all


def monday(day):
    return day - datetime.timedelta(days=day.weekday())


@dataclass(frozen=True)
class Filter:
    minggu: datetime.date
    mode: str = ModeKalkulasi.SEMUA
    jenis: str = ''
    site: int | None = None
    perusahaan: int | None = None
    dengan_ptz: bool = False

    @classmethod
    def from_query(cls, query):
        """?minggu=YYYY-MM-DD&mode=&jenis=&site=&perusahaan= -> Filter. Unknown values fall back to the default."""
        try:
            minggu = monday(datetime.date.fromisoformat(query.get('minggu', '')))
        except ValueError:
            minggu = this_monday()
        mode = query.get('mode', '')
        jenis = query.get('jenis', '')
        site, perusahaan = query.get('site', ''), query.get('perusahaan', '')
        return cls(
            minggu=min(minggu, this_monday()),
            mode=mode if mode in ModeKalkulasi.values else ModeKalkulasi.SEMUA,
            jenis=jenis if jenis in JenisArea.values else '',
            site=int(site) if site.isdigit() else None,
            perusahaan=int(perusahaan) if perusahaan.isdigit() else None,
            dengan_ptz=query.get('dengan_ptz') == '1',
        )

    @property
    def live(self):
        return self.minggu == this_monday()

    def query(self, **changes):
        """The filter as query-string pairs (empty values left out), with `changes` applied."""
        values = {'minggu': self.minggu.isoformat(), 'mode': self.mode, 'jenis': self.jenis,
                  'site': self.site or '', 'perusahaan': self.perusahaan or '',
                  'dengan_ptz': '1' if self.dengan_ptz else '', **changes}
        return {key: value for key, value in values.items() if value not in ('', None)}


# --- Rows ---------------------------------------------------------------------------------------

def _names(f):
    """Locked weeks keep the site and company names (a retired Mitra Site loses its link)."""
    site = Site.objects.filter(pk=f.site).values_list('kode', flat=True).first() if f.site else None
    perusahaan = Perusahaan.objects.filter(pk=f.perusahaan).values_list('nama', flat=True).first() if f.perusahaan else None
    return site, perusahaan


def _live(f):
    rows = CoverageLive.objects.filter(
        mode=f.mode, dengan_ptz=f.dengan_ptz, mitra_site__aktif=True,
    ).select_related('mitra_site__site', 'mitra_site__perusahaan')
    if f.site:
        rows = rows.filter(mitra_site__site_id=f.site)
    if f.perusahaan:
        rows = rows.filter(mitra_site__perusahaan_id=f.perusahaan)
    return rows


def _locked(f, minggu):
    rows = SnapshotCoverageJenis.objects.filter(
        mode=f.mode, dengan_ptz=f.dengan_ptz, snapshot__status=SnapshotCoverage.Status.TERKUNCI,
    )
    rows = rows.filter(snapshot__minggu__in=minggu) if isinstance(minggu, (list, tuple)) else rows.filter(snapshot__minggu=minggu)
    site, perusahaan = _names(f)
    if f.site:
        rows = rows.filter(snapshot__site_kode=site)
    if f.perusahaan:
        rows = rows.filter(snapshot__perusahaan_nama=perusahaan)
    return rows.select_related('snapshot')


def baris(f, geom=False):
    """The rows of week `f.minggu` for the filter: CoverageLive this week, the locked snapshot otherwise."""
    rows = _live(f) if f.live else _locked(f, f.minggu)
    if f.jenis:
        rows = rows.filter(jenis=f.jenis)
    else:
        rows = rows.filter(jenis__in=JENIS_UTAMA)
    if not geom:
        rows = rows.defer(*(f'geom_{name}' for name in LAPISAN))
    return rows


def baris_high_risk(f, geom=False):
    """High Risk rows under the same week/site/company/mode filters, independent of the kind filter."""
    return baris(replace(f, jenis=JenisArea.HIGH_RISK), geom=geom)


def asal(row):
    """(site, perusahaan, minggu_area, fallback, catatan) of a row, live or locked."""
    if isinstance(row, CoverageLive):
        return (row.mitra_site.site.kode, row.mitra_site.perusahaan.nama, row.minggu_area, row.fallback, row.catatan)
    snap = row.snapshot
    return (snap.site_kode, snap.perusahaan_nama, snap.minggu_area, snap.fallback, snap.catatan)


def menunggu(f):
    """Mitra Sites whose closed week is still waiting for its work area (no numbers yet)."""
    if f.live:
        return []
    snaps = SnapshotCoverage.objects.filter(minggu=f.minggu, status=SnapshotCoverage.Status.MENUNGGU)
    site, perusahaan = _names(f)
    if f.site:
        snaps = snaps.filter(site_kode=site)
    if f.perusahaan:
        snaps = snaps.filter(perusahaan_nama=perusahaan)
    return [{'site': s.site_kode, 'perusahaan': s.perusahaan_nama, 'batas_tunggu': s.batas_tunggu}
            for s in snaps.order_by('site_kode', 'perusahaan_nama')]


def kpi(rows, mode):
    """Totals over the rows: ha and percent (kalkulasi.ringkasan) and how many Mitra Sites they cover."""
    rows = list(rows)
    summary = kalkulasi.ringkasan(rows, mode)
    summary['mitra_site'] = len({asal(r)[:2] for r in rows})
    return summary


def kpi_high_risk(rows, mode):
    """High Risk totals kept separate because this boundary may overlap every operational kind."""
    rows = list(rows)
    summary = kalkulasi.ringkasan(rows, mode, jenis=[JenisArea.HIGH_RISK])
    summary['mitra_site'] = len({asal(r)[:2] for r in rows if r.jenis == JenisArea.HIGH_RISK})
    return summary


def tabel(rows, mode):
    """One entry per site x perusahaan: its total and one line per kind, sorted by site then company."""
    groups = {}
    for row in rows:
        site, perusahaan, minggu_area, fallback, catatan = asal(row)
        group = groups.setdefault((site, perusahaan), {
            'site': site, 'perusahaan': perusahaan, 'minggu_area': minggu_area, 'minggu_area_label': minggu_label(minggu_area),
            'fallback': fallback, 'catatan': catatan, 'rows': [],
        })
        group['rows'].append(row)
    result = []
    for key in sorted(groups):
        group = groups[key]
        rows_ = sorted(group.pop('rows'), key=lambda r: JENIS_URUT.index(r.jenis))
        group['total'] = kalkulasi.ringkasan(rows_, mode)
        group['high_risk'] = kalkulasi.ringkasan(rows_, mode, jenis=[JenisArea.HIGH_RISK])
        group['jenis'] = [{
            'jenis': r.jenis, 'label': r.get_jenis_display(), 'luas': r.luas_ha, 'tercover': r.tercover_ha,
            'potensi': r.potensi_ha, 'tidak': r.tidak_ha, 'jumlah_cctv': r.jumlah_cctv,
            'persen': {'tercover': r.persen_tercover, 'potensi': r.persen_potensi, 'tidak': r.persen_tidak},
        } for r in rows_ if r.luas_ha]
        result.append(group)
    return result


def _point(minggu, luas, tercover, potensi, tidak):
    luas = luas or 0
    values = {'tercover': tercover or 0, 'potensi': potensi or 0, 'tidak': tidak or 0}
    persen = {key: (min(100.0, round(100 * value / luas, 1)) if luas else None) for key, value in values.items()}
    return {'minggu': minggu.isoformat(), 'label': minggu_label(minggu), 'luas': round(luas, 4),
            **{key: round(value, 4) for key, value in values.items()}, 'persen': persen}


def tren(f, n=TREN_MINGGU):
    """The `n` weeks up to `f.minggu`, oldest first; a week without numbers has luas 0 and no percent."""
    return _series(f, [f.minggu - datetime.timedelta(days=7 * i) for i in reversed(range(n))])


def garis_waktu(f):
    """The weeks of the fullscreen timeline, oldest first: at least the last GARIS_WAKTU_MINGGU weeks up
    to this week, further back when a closed week is older. Each carries its % tercover for the filter
    and a status (live / terkunci / menunggu / kosong); weeks before any snapshot are simply 'kosong'.
    The page pads the line with faded weeks on both sides to fit its width (never picked)."""
    year_ago = this_monday() - datetime.timedelta(days=7 * (GARIS_WAKTU_MINGGU - 1))
    oldest = SnapshotCoverage.objects.order_by('minggu').values_list('minggu', flat=True).first()
    first = min(oldest, year_ago) if oldest else year_ago
    count = (this_monday() - first).days // 7 + 1
    weeks = [first + datetime.timedelta(days=7 * i) for i in range(count)]
    waiting = set(SnapshotCoverage.objects.filter(status=SnapshotCoverage.Status.MENUNGGU).values_list('minggu', flat=True))
    result = []
    for week, point in zip(weeks, _series(f, weeks)):
        akhir = week + datetime.timedelta(days=6)
        status = ('live' if week == this_monday() else 'terkunci' if point['persen']['tercover'] is not None
                  else 'menunggu' if week in waiting else 'kosong')
        result.append({'minggu': point['minggu'], 'label': point['label'], 'w': week.strftime('W%V'),
                       'bulan': week.month, 'tahun': week.year, 'rentang': f'{week:%d %b} – {akhir:%d %b %Y}',
                       'tercover': point['persen']['tercover'], 'status': status})
    return result


def _series(f, weeks):
    """Totals for each of `weeks` (Mondays): the locked snapshots, plus the live rows for this week."""
    sums = dict(luas=Sum('luas_ha'), tercover=Sum('tercover_ha'), potensi=Sum('potensi_ha'), tidak=Sum('tidak_ha'))
    locked = _locked(f, weeks)
    if f.jenis:
        locked = locked.filter(jenis=f.jenis)
    else:
        locked = locked.filter(jenis__in=JENIS_UTAMA)
    by_week = {row.pop('snapshot__minggu'): row for row in locked.values('snapshot__minggu').annotate(**sums)}
    if this_monday() in weeks:
        live = _live(f)
        if f.jenis:
            live = live.filter(jenis=f.jenis)
        else:
            live = live.filter(jenis__in=JENIS_UTAMA)
        by_week[this_monday()] = live.aggregate(**sums)
    empty = dict.fromkeys(sums)
    return [_point(week, **by_week.get(week, empty)) for week in weeks]


# --- Map ----------------------------------------------------------------------------------------

def _simplified(field):
    """`field` simplified by SIMPLIFY_M metres (in UTM 50N), back in WGS 84, as GeoJSON text."""
    utm = Transform(field, UTM_50N)
    simple = Func(utm, SIMPLIFY_M, function='ST_SimplifyPreserveTopology', output_field=GeometryField(srid=UTM_50N))
    return AsGeoJSON(Transform(simple, 4326), precision=6)


def _collection(features):
    return {'type': 'FeatureCollection', 'features': features}


def geojson(f, lapisan):
    """One of the coverage layers (tercover / potensi / tidak) of the filtered rows."""
    field = f'geom_{lapisan}'
    rows = baris(f, geom=True).exclude(**{f'{field}__isnull': True}).annotate(peta=_simplified(field))
    features = []
    for row in rows.defer(*(f'geom_{name}' for name in LAPISAN)):
        site, perusahaan, *_ = asal(row)
        features.append({'type': 'Feature', 'geometry': json.loads(row.peta), 'properties': {
            'site': site, 'perusahaan': perusahaan, 'jenis': row.jenis, 'jenis_label': row.get_jenis_display(),
            'ha': round(getattr(row, f'{lapisan}_ha'), 4),
        }})
    return _collection(features)


def _area_features(pengajuan, site, perusahaan, jenis):
    features = []
    for area in pengajuan.area.all():
        if area.geom is None or (jenis and area.jenis != jenis):
            continue
        features.append({'type': 'Feature', 'geometry': json.loads(area.geom.geojson), 'properties': {
            'jenis': area.jenis, 'jenis_label': area.get_jenis_display(), 'site': site, 'perusahaan': perusahaan,
            'minggu': pengajuan.minggu_label,
        }})
    return features


def area_kerja(f):
    """Outlines of the work areas used in week `f.minggu` (in force this week, or as locked)."""
    features = []
    if f.live:
        sites = MitraSite.objects.aktif().select_related('site', 'perusahaan')
        if f.site:
            sites = sites.filter(site_id=f.site)
        if f.perusahaan:
            sites = sites.filter(perusahaan_id=f.perusahaan)
        for mitra_site in sites:
            pengajuan = services.area_berlaku(mitra_site, f.minggu)['pengajuan']
            if pengajuan:
                features += _area_features(pengajuan, mitra_site.site.kode, mitra_site.perusahaan.nama, f.jenis)
        return _collection(features)
    snaps = SnapshotCoverage.objects.filter(minggu=f.minggu, pengajuan__isnull=False).select_related('pengajuan')
    site, perusahaan = _names(f)
    if f.site:
        snaps = snaps.filter(site_kode=site)
    if f.perusahaan:
        snaps = snaps.filter(perusahaan_nama=perusahaan)
    for snap in snaps.prefetch_related('pengajuan__area'):
        features += _area_features(snap.pengajuan, snap.site_kode, snap.perusahaan_nama, f.jenis)
    return _collection(features)


def _xy(field):
    return (Func(F(field), function='ST_X', output_field=FloatField()),
            Func(F(field), function='ST_Y', output_field=FloatField()))


def cctv_titik(f):
    """CCTV points of week `f.minggu`: the live table this week, the CCTV snapshot of a closed week."""
    if f.live:
        rows = Cctv.objects.exclude(posisi__isnull=True)
        if not f.dengan_ptz:
            rows = rows.exclude(jenis__in=PTZ)
        if f.site:
            rows = rows.filter(site_id=f.site)
        if f.perusahaan:
            rows = rows.filter(perusahaan_id=f.perusahaan)
        lon, lat = _xy('posisi')
        rows = rows.annotate(lon=lon, lat=lat).values_list(
            'pk', 'no_cctv', 'nama', 'jenis', 'kondisi', 'status', 'lon', 'lat',
        )
    else:
        rows = SnapshotMingguan.objects.filter(minggu=f.minggu).exclude(posisi__isnull=True)
        if not f.dengan_ptz:
            rows = rows.exclude(data__jenis__in=PTZ)
        if f.site:
            rows = rows.filter(data__site_id=f.site)
        if f.perusahaan:
            rows = rows.filter(data__perusahaan_id=f.perusahaan)
        lon, lat = _xy('posisi')
        rows = rows.annotate(lon=lon, lat=lat).values_list(
            'cctv_id', 'no_cctv', 'data__nama', 'data__jenis', 'data__kondisi', 'data__status', 'lon', 'lat')
    features = [{
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [round(lon, 6), round(lat, 6)]},
        'properties': {'id': pk, 'no': no, 'nama': nama, 'jenis': jenis, 'kondisi': kondisi, 'status': status,
                       'kelompok': kalkulasi.kelompok(status, kondisi)},
    } for pk, no, nama, jenis, kondisi, status, lon, lat in rows.iterator(chunk_size=2000)]
    return _collection(features)


BOUNDARY_SIMPLIFY_DEG = 0.000005  # ~0.5 m around Berau; the boundaries are drawn, not measured, here


def cctv_boundary(f):
    """The CCTV coverage boundaries themselves (one polygon per camera) for week `f.minggu`: as the
    map shows them this week (cctv.snapshot.boundaries), or as the CCTV snapshot locked them."""
    if f.live:
        from cctv.snapshot import boundaries

        geom_of = boundaries()
        rows = Cctv.objects.filter(pk__in=list(geom_of))
        if not f.dengan_ptz:
            rows = rows.exclude(jenis__in=PTZ)
        if f.site:
            rows = rows.filter(site_id=f.site)
        if f.perusahaan:
            rows = rows.filter(perusahaan_id=f.perusahaan)
        cameras = [(pk, no, nama, jenis, kondisi, status, geom_of[pk])
                   for pk, no, nama, jenis, kondisi, status in rows.values_list(
                       'pk', 'no_cctv', 'nama', 'jenis', 'kondisi', 'status')]
    else:
        rows = SnapshotMingguan.objects.filter(minggu=f.minggu).exclude(coverage__isnull=True)
        if not f.dengan_ptz:
            rows = rows.exclude(data__jenis__in=PTZ)
        if f.site:
            rows = rows.filter(data__site_id=f.site)
        if f.perusahaan:
            rows = rows.filter(data__perusahaan_id=f.perusahaan)
        cameras = [(pk, no, data.get('nama'), data.get('jenis'), data.get('kondisi'), data.get('status'), geom)
                   for pk, no, data, geom in rows.values_list('cctv_id', 'no_cctv', 'data', 'coverage')]
    features = []
    for pk, no, nama, jenis, kondisi, status, geom in cameras:
        if geom is None or geom.empty:
            continue
        shape = geom.simplify(BOUNDARY_SIMPLIFY_DEG, preserve_topology=True)
        features.append({'type': 'Feature', 'geometry': json.loads(shape.geojson), 'properties': {
            'id': pk, 'no': no, 'nama': nama, 'jenis': jenis, 'kondisi': kondisi,
            'kelompok': kalkulasi.kelompok(status, kondisi),
        }})
    return _collection(features)


# --- Filter options -----------------------------------------------------------------------------

def daftar_minggu():
    """Weeks with numbers: this week and every closed week, newest first."""
    closed = SnapshotCoverage.objects.order_by('-minggu').values_list('minggu', flat=True).distinct()
    return [this_monday(), *[week for week in closed if week != this_monday()]]


def pilihan():
    """Sites and companies for the filters: those with a Mitra Site, active or not."""
    sites = Site.objects.filter(pk__in=MitraSite.objects.values('site_id')).order_by('kode')
    perusahaan = Perusahaan.objects.filter(pk__in=MitraSite.objects.values('perusahaan_id')).order_by('nama')
    return sites, perusahaan


def dihitung_pada(f):
    """When the numbers were last computed: the latest live recompute, or the lock time."""
    if f.live:
        return _live(f).order_by('-dihitung_pada').values_list('dihitung_pada', flat=True).first()
    snaps = SnapshotCoverage.objects.filter(minggu=f.minggu, status=SnapshotCoverage.Status.TERKUNCI)
    return snaps.order_by('-terkunci_pada').values_list('terkunci_pada', flat=True).first()
