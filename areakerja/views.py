"""Work areas: the weekly list per Mitra Site, and the request page (Pengajuan / Verifikasi BC / Sah & berlaku)."""

import datetime
import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from cctv.access import get_scope
from cctv.coverage import GeometryError, ReviewError, this_monday
from cctv.models import Cctv
from cctv.snapshot import boundaries
from orthophoto.models import Orthophoto

from . import access, kalkulasi, services
from .forms import AjukanForm, AnotasiForm, SlotForm, VerifikasiForm
from .models import (
    AreaKerja, CoverageLive, Departemen, JenisArea, MitraSite, PengajuanAreaKerja, SnapshotCoverage,
    SnapshotCoverageJenis,
)

Status = PengajuanAreaKerja.Status
WARNA = {'pit': '9C6B3C', 'hauling': 'D9822B', 'infra_tambang': '2F9E5B', 'infra_non_tambang': '4C7DBF'}
TABS = (('pengajuan', 'Pengajuan area kerja'), ('sgi', 'Verifikasi BC'), ('selesai', 'Sah & berlaku'))
# PT Berau Coal: Survey BC uploads; the same Verifikasi BC follows.
TABS_GABUNG = (('pengajuan', 'Upload area kerja Survey BC'), ('sgi', 'Verifikasi BC'), ('selesai', 'Sah & berlaku'))
TAHAP = {Status.DRAFT: 'pengajuan', Status.RETURN_MITRA: 'pengajuan', Status.MENUNGGU_SGI: 'sgi', Status.APPROVED: 'selesai'}


def _scope(user):
    scope = get_scope(user)
    if not scope.can_open:
        raise PermissionDenied
    return scope


def _week(value):
    """?minggu=YYYY-MM-DD -> that week's Monday (default: this week)."""
    try:
        day = datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return this_monday()
    return day - datetime.timedelta(days=day.weekday())


def _url(pengajuan, tab=None):
    url = reverse('areakerja:pengajuan', args=[pengajuan.pk])
    return f'{url}?tab={tab}' if tab else url


@login_required
def index(request):
    scope = _scope(request.user)
    minggu = _week(request.GET.get('minggu'))
    semua = access.visible(scope)
    site_options = sorted({m.site for m in semua}, key=lambda s: s.kode)
    perusahaan_options = sorted({m.perusahaan for m in semua}, key=lambda p: p.nama)
    site_dipilih = request.GET.get('site', '')
    perusahaan_dipilih = request.GET.get('perusahaan', '')
    sites = semua
    if site_dipilih.isdigit():
        sites = sites.filter(site_id=site_dipilih)
    if perusahaan_dipilih.isdigit():
        sites = sites.filter(perusahaan_id=perusahaan_dipilih)
    # The filters, kept when moving to another week.
    filter_qs = urlencode({k: v for k, v in (('site', site_dipilih), ('perusahaan', perusahaan_dipilih)) if v.isdigit()})
    pengajuan = {
        p.mitra_site_id: p for p in PengajuanAreaKerja.objects.filter(minggu=minggu, mitra_site__in=sites)
    }
    # Coverage (official mode): live for the running week, the locked snapshot for a closed one.
    hasil, snapshot = {}, {}
    if minggu == this_monday():
        for row in CoverageLive.objects.filter(mitra_site__in=sites).defer('geom_tercover', 'geom_potensi', 'geom_tidak'):
            hasil.setdefault(row.mitra_site_id, []).append(row)
    else:
        for snap in SnapshotCoverage.objects.filter(minggu=minggu, mitra_site__in=sites).prefetch_related(
                Prefetch('hasil', SnapshotCoverageJenis.objects.defer('geom_tercover', 'geom_potensi', 'geom_tidak'))):
            snapshot[snap.mitra_site_id] = snap
            hasil[snap.mitra_site_id] = list(snap.hasil.all())
    rows = []
    for mitra_site in sites:
        berlaku = services.area_berlaku(mitra_site, minggu)
        rows.append({
            'mitra_site': mitra_site,
            'pengajuan': pengajuan.get(mitra_site.pk),
            'berlaku': berlaku,
            'can_upload': access.can_upload(scope, mitra_site) and minggu == this_monday(),
            'coverage': kalkulasi.ringkasan(hasil[mitra_site.pk]) if hasil.get(mitra_site.pk) else None,
            'snapshot': snapshot.get(mitra_site.pk),
        })
    counts = {
        'total': len(rows),
        'approved': sum(1 for r in rows if r['pengajuan'] and r['pengajuan'].status == Status.APPROVED),
        'menunggu': sum(1 for r in rows if r['pengajuan'] and r['pengajuan'].status == Status.MENUNGGU_SGI),
        'fallback': sum(1 for r in rows if r['berlaku']['fallback']),
    }
    return render(request, 'areakerja/index.html', {
        'rows': rows, 'counts': counts, 'minggu': minggu, 'minggu_ini': this_monday(),
        'akhir': minggu + datetime.timedelta(days=6), 'sebelum': minggu - datetime.timedelta(days=7), 'sesudah': minggu + datetime.timedelta(days=7),
        'sites': site_options, 'site_dipilih': site_dipilih,
        'perusahaan_list': perusahaan_options, 'perusahaan_dipilih': perusahaan_dipilih, 'filter_qs': filter_qs,
        'week_picker': {'dilihat': minggu.isoformat(), 'berjalan': this_monday().isoformat(), 'filter': filter_qs},
    })


@login_required
@require_POST
def buka(request, mitra_pk):
    """'Ajukan area kerja' on the list: open this week's request for the Mitra Site (Draft if new)."""
    scope = _scope(request.user)
    mitra_site = get_object_or_404(access.visible(scope), pk=mitra_pk)
    if not access.can_upload(scope, mitra_site):
        raise PermissionDenied
    try:
        pengajuan = services.request_for(mitra_site, request.user)
    except ReviewError as exc:
        messages.error(request, exc.message)
        return redirect('areakerja:index')
    return redirect(_url(pengajuan, 'pengajuan'))


def _get(request, pk):
    scope = _scope(request.user)
    queryset = PengajuanAreaKerja.objects.select_related(
        'mitra_site__site', 'mitra_site__perusahaan', 'diajukan_oleh', 'disetujui_oleh',
    ).prefetch_related('area')
    pengajuan = get_object_or_404(queryset.filter(mitra_site__in=access.visible(scope)), pk=pk)
    return scope, pengajuan


def _feature(geom):
    return json.loads(geom.geojson) if geom else None


def _extent(*geoms):
    geoms = [g for g in geoms if g]
    if not geoms:
        ortho = Orthophoto.objects.filter(aktif=True).only('batas').first()
        return list(ortho.batas.extent) if ortho and ortho.batas else None
    union = geoms[0]
    for g in geoms[1:]:
        union = union.union(g)
    return list(union.extent)


@login_required
def page(request, pk):
    scope, pengajuan = _get(request, pk)
    tabs = TABS_GABUNG if pengajuan.gabung else TABS
    keys = [key for key, _ in tabs]
    tahap = TAHAP[pengajuan.status]
    tahap = tahap if tahap in keys else 'pengajuan'
    # Once Approved only Sah & berlaku opens: the earlier steps are done and stay closed.
    selesai = pengajuan.status == Status.APPROVED
    tab = request.GET.get('tab') if request.GET.get('tab') in keys and not selesai else tahap
    reached = keys.index(tahap)
    lalu = services.area_berlaku(pengajuan.mitra_site, pengajuan.minggu - datetime.timedelta(days=7))
    slots = []
    for jenis in JenisArea:
        area = pengajuan.slot(jenis.value)
        lama = lalu['pengajuan'].slot(jenis.value) if lalu['pengajuan'] else None
        slots.append({'jenis': jenis, 'area': area, 'lalu': lama, 'warna': WARNA[jenis.value]})
    can_edit = services.editable(pengajuan) and access.can_upload(scope, pengajuan.mitra_site)
    menunggu = pengajuan.status == Status.MENUNGGU_SGI
    mine = access.departemen(scope)
    done = {v.departemen: v for v in pengajuan.verifikasi.select_related('user')}
    verifikasi = [{'departemen': dept, 'hasil': done.get(dept.value), 'bisa': menunggu and dept.value in mine}
                  for dept in Departemen]
    anotasi = list(pengajuan.anotasi.select_related('user', 'selesai_oleh'))
    peta = {
        'jenis': [{'id': s['jenis'].value, 'label': s['jenis'].label, 'warna': '#' + s['warna'],
                   'geom': _feature(s['area'].geom if s['area'] else None),
                   'lalu': _feature(s['lalu'].geom if s['lalu'] else None)} for s in slots],
        'extent': _extent(*[s['area'].geom for s in slots if s['area']], *[s['lalu'].geom for s in slots if s['lalu']]),
        'anotasi': [_anotasi_json(a, request.user) for a in anotasi],
    }
    return render(request, 'areakerja/pengajuan.html', {
        'p': pengajuan, 'tab': tab,
        'tabs': [(key, label, i < reached, i == reached, not selesai or key == tahap) for i, (key, label) in enumerate(tabs)],
        'tab_terbuka': keys[:reached + 1],
        'slots': slots, 'lalu': lalu, 'peta': peta,
        'total_ha': round(sum(s['area'].luas_ha or 0 for s in slots if s['area']), 2),
        'lengkap': all(s['area'] for s in slots),
        'can': {
            'edit': can_edit,
            'ajukan': can_edit and pengajuan.status in (Status.DRAFT, Status.RETURN_MITRA),
            'verify': bool(mine) and menunggu,
            'anotasi': bool(mine) and menunggu,
        },
        'verifikasi': verifikasi, 'departemen_saya': mine, 'departemen_json': json.dumps(mine), 'anotasi': anotasi,
        'sumber_choices': AreaKerja.Sumber.choices,
        'riwayat': pengajuan.riwayat.select_related('user'),
    })


@login_required
@require_POST
def slot(request, pk):
    scope, pengajuan = _get(request, pk)
    if not access.can_upload(scope, pengajuan.mitra_site):
        raise PermissionDenied
    form = SlotForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, '; '.join(e for errors in form.errors.values() for e in errors))
        return redirect(_url(pengajuan, 'pengajuan'))
    data = form.cleaned_data
    try:
        area = services.save_slot(pengajuan, request.user, data['jenis'], data['sumber'], data.get('file_shp'))
    except (GeometryError, ReviewError) as exc:
        messages.error(request, f"{JenisArea(data['jenis']).label}: {getattr(exc, 'message', None) or exc}")
        return redirect(_url(pengajuan, 'pengajuan'))
    note = f' ({area.dipotong_ha:,.2f} ha dipotong karena tumpang tindih dengan jenis berprioritas lebih tinggi)' if area.dipotong_ha else ''
    messages.success(request, f'{area.get_jenis_display()}: {area.get_sumber_display()}, {area.luas_ha or 0:,.2f} ha{note}.')
    return redirect(_url(pengajuan, 'pengajuan'))


@login_required
@require_POST
def ajukan(request, pk):
    scope, pengajuan = _get(request, pk)
    if not access.can_upload(scope, pengajuan.mitra_site):
        raise PermissionDenied
    form = AjukanForm(request.POST)
    form.is_valid()
    try:
        services.ajukan(pengajuan, request.user, form.cleaned_data.get('catatan', ''))
    except ReviewError as exc:
        messages.error(request, exc.message)
        return redirect(_url(pengajuan, 'pengajuan'))
    messages.success(request, f'Area kerja {pengajuan}: {pengajuan.get_status_display()}.')
    return redirect(_url(pengajuan))


@login_required
@require_POST
def verifikasi(request, pk):
    """One department's Disetujui, or sending the request back to the uploader."""
    scope, pengajuan = _get(request, pk)
    form = VerifikasiForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Data verifikasi tidak valid.')
        return redirect(_url(pengajuan, 'sgi'))
    data = form.cleaned_data
    if data['departemen'] not in access.departemen(scope):
        raise PermissionDenied
    try:
        services.verifikasi(pengajuan, request.user, departemen=data['departemen'], aksi=data['aksi'],
                            catatan=data['catatan'])
    except ReviewError as exc:
        messages.error(request, exc.message)
        return redirect(_url(pengajuan, 'sgi'))
    label = Departemen(data['departemen']).label
    if pengajuan.status == Status.APPROVED:
        messages.success(request, f'{label} menyetujui. SGI, Mining, dan Safety BC lengkap: area kerja Approved.')
    elif pengajuan.status == Status.MENUNGGU_SGI:
        messages.success(request, f'{label} menyetujui. Menunggu departemen lain.')
    else:
        messages.success(request, f'Area kerja {pengajuan}: {pengajuan.status_label}.')
    return redirect(_url(pengajuan))


def _anotasi_json(anotasi, user):
    return {
        'id': anotasi.pk, 'lon': anotasi.posisi.x, 'lat': anotasi.posisi.y, 'teks': anotasi.teks,
        'bentuk': anotasi.bentuk, 'bentuk_label': anotasi.get_bentuk_display(), 'ukuran': anotasi.ukuran,
        'geom': json.loads(anotasi.geom.geojson) if anotasi.geom else None,
        'departemen': anotasi.get_departemen_display(), 'kode': anotasi.departemen,
        'oleh': (anotasi.user.get_full_name() or anotasi.user.get_username()) if anotasi.user else '-',
        'waktu': timezone.localtime(anotasi.dibuat).strftime('%d %b %H:%M'),
        'selesai': anotasi.selesai, 'milik_saya': anotasi.user_id == user.pk,
    }


@login_required
@require_POST
def anotasi_tambah(request, pk):
    """A reviewer adds a note on the map, as a point, a line or an area: JSON in, JSON out."""
    scope, pengajuan = _get(request, pk)
    form = AnotasiForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': 'Data anotasi tidak valid.'}, status=400)
    data = form.cleaned_data
    if data['departemen'] not in access.departemen(scope):
        raise PermissionDenied
    try:
        anotasi = services.tambah_anotasi(pengajuan, request.user, data['departemen'], data['teks'],
                                          lon=data['lon'], lat=data['lat'], geometri=data['geometri'])
    except ReviewError as exc:
        return JsonResponse({'error': exc.message}, status=400)
    return JsonResponse(_anotasi_json(anotasi, request.user))


@login_required
@require_POST
def anotasi_hapus(request, pk, anotasi_pk):
    _, pengajuan = _get(request, pk)
    anotasi = get_object_or_404(pengajuan.anotasi, pk=anotasi_pk)
    try:
        services.hapus_anotasi(anotasi, request.user)
    except ReviewError as exc:
        return JsonResponse({'error': exc.message}, status=400)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def anotasi_tandai(request, pk, anotasi_pk):
    """The uploader ticks a reviewer's note as solved (or unticks it)."""
    scope, pengajuan = _get(request, pk)
    if not access.can_upload(scope, pengajuan.mitra_site):
        raise PermissionDenied
    anotasi = get_object_or_404(pengajuan.anotasi, pk=anotasi_pk)
    try:
        services.tandai_anotasi(anotasi, request.user, request.POST.get('selesai') == '1')
    except ReviewError as exc:
        messages.error(request, exc.message)
    return redirect(_url(pengajuan, 'pengajuan'))


@login_required
def cctv_sekitar(request, pk):
    """CCTV around the request's areas, for the verification map: {titik, coverage} GeoJSON.
    `milik` marks the Mitra Site's own cameras (the official mode); the rest are other companies'."""
    _, pengajuan = _get(request, pk)
    areas = [area.geom for area in pengajuan.area.all() if area.geom]
    empty = {'type': 'FeatureCollection', 'features': []}
    if not areas:
        return JsonResponse({'titik': empty, 'coverage': empty})
    window = _union(areas).envelope.buffer(0.005)  # ~550 m around, for cameras looking in from outside
    boundary_of = boundaries()
    titik, coverage = [], []
    rows = Cctv.objects.filter(Q(posisi__intersects=window) | Q(pk__in=[pk_ for pk_, g in boundary_of.items() if g and g.intersects(window)]))
    for cctv in rows.only('pk', 'no_cctv', 'nama', 'site_id', 'perusahaan_id', 'status', 'kondisi', 'posisi'):
        props = {
            'no': cctv.no_cctv, 'nama': cctv.nama, 'kondisi': cctv.kondisi or '',
            'kelompok': kalkulasi.kelompok(cctv.status, cctv.kondisi) or 'dicabut',
            'milik': (cctv.site_id, cctv.perusahaan_id) == (pengajuan.mitra_site.site_id, pengajuan.mitra_site.perusahaan_id),
        }
        if cctv.posisi:
            titik.append({'type': 'Feature', 'geometry': json.loads(cctv.posisi.geojson), 'properties': props})
        geom = boundary_of.get(cctv.pk)
        if geom is not None and props['kelompok'] != 'dicabut':
            coverage.append({'type': 'Feature', 'geometry': json.loads(geom.geojson), 'properties': props})
    return JsonResponse({'titik': {'type': 'FeatureCollection', 'features': titik},
                         'coverage': {'type': 'FeatureCollection', 'features': coverage}})


def _union(geoms):
    union = geoms[0]
    for geom in geoms[1:]:
        union = union.union(geom)
    return union


@login_required
def peta_berlaku(request):
    """GeoJSON of the work areas in force this week, every active Mitra Site together, for the CCTV maps.
    One feature per Mitra Site and kind; properties say which, and whether it is last week's (fallback)."""
    _scope(request.user)
    minggu = this_monday()
    features = []
    for mitra_site in MitraSite.objects.aktif().select_related('site', 'perusahaan'):
        berlaku = services.area_berlaku(mitra_site, minggu)
        if not berlaku['pengajuan']:
            continue
        for area in berlaku['pengajuan'].area.all():
            if area.geom is None:
                continue
            features.append({
                'type': 'Feature',
                'geometry': json.loads(area.geom.geojson),
                'properties': {
                    'jenis': area.jenis, 'jenis_label': area.get_jenis_display(),
                    'site': mitra_site.site.kode, 'perusahaan': mitra_site.perusahaan.nama,
                    'minggu': berlaku['pengajuan'].minggu_label, 'fallback': berlaku['fallback'],
                },
            })
    response = JsonResponse({'type': 'FeatureCollection', 'features': features})
    response['Cache-Control'] = 'private, max-age=300'
    return response
