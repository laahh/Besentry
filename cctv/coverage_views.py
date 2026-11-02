"""Coverage verification: request queue, side-by-side comparison, SGI/Safety checklists."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from orthophoto.models import Orthophoto

from . import coverage, homography
from .access import get_scope
from .coverage_forms import SCREENSHOT_MAX_MB, PengajuanForm, ReviewForm, is_image
from .models import Cctv, PengajuanCoverage

Status = PengajuanCoverage.Status
TABS = (Status.MENUNGGU_DIGITASI, Status.MENUNGGU_SGI, Status.MENUNGGU_SAFETY, Status.DIKEMBALIKAN, Status.SELESAI)
QUEUE_LIMIT = 100


def _scope(user):
    scope = get_scope(user)
    if not (scope.can_open or scope.sees_all_coverage):
        raise PermissionDenied
    return scope


def _visible(scope):
    if scope.sees_all_coverage:  # SGI and Safety work on every site
        return PengajuanCoverage.objects.all()
    return scope.filter(PengajuanCoverage.objects.all(), prefix='cctv__')


def _feature(geom):
    return json.loads(geom.geojson) if geom else None


def _kalibrasi_json(kalibrasi):
    """The camera's GCPs for the GeoPoint panel (pixels in the calibrated image's size)."""
    if not kalibrasi:
        return None
    return {'gcp': kalibrasi.gcp, 'lebar': kalibrasi.lebar, 'tinggi': kalibrasi.tinggi,
            'rmse_m': kalibrasi.hasil.get('rmse_m'), 'inliers': kalibrasi.hasil.get('inliers', [])}


def _comparison(pengajuan):
    """Everything the two maps and the figures under them need."""
    cctv = pengajuan.cctv
    active, versi_aktif = coverage.active_coverage(cctv)
    shapes = [g for g in (active, pengajuan.geom, cctv.posisi) if g]
    extent = None
    if shapes:
        union = shapes[0]
        for shape in shapes[1:]:
            union = union.union(shape)
        extent = list(union.extent)
    return {
        'versi_aktif': versi_aktif,
        'luas_aktif': coverage.luas_ha(active),
        'luas_usulan': coverage.luas_ha(pengajuan.geom),
        'peta': {
            'aktif': _feature(active),
            'usulan': _feature(pengajuan.geom),
            'kamera': list(cctv.posisi.coords) if cctv.posisi else None,
            'extent': extent,
            'kalibrasi': _kalibrasi_json(coverage.active_kalibrasi(cctv)),
            'area_pandang': pengajuan.analisis.get('area_pandang'),
            'cctv': cctv.no_cctv,
        },
    }


def _stage_marks(pengajuan):
    """Badge per checklist card: 'aktif', 'disetujui', 'berikutnya' or ''."""
    status = pengajuan.status
    sgi = {Status.MENUNGGU_DIGITASI: 'berikutnya', Status.MENUNGGU_SGI: 'aktif', Status.MENUNGGU_SAFETY: 'disetujui', Status.SELESAI: 'disetujui'}
    safety = {Status.MENUNGGU_DIGITASI: 'berikutnya', Status.MENUNGGU_SGI: 'berikutnya', Status.MENUNGGU_SAFETY: 'aktif', Status.SELESAI: 'disetujui'}
    return {'tanda_sgi': sgi.get(status, ''), 'tanda_safety': safety.get(status, '')}


@login_required
def index(request):
    scope = _scope(request.user)
    visible = _visible(scope)
    status = request.GET.get('status')
    if status not in TABS:
        status = Status.MENUNGGU_SGI
    counts = visible.aggregate(**{tab: Count('pk', filter=Q(status=tab)) for tab in TABS})
    queue = list(
        visible.filter(status=status).select_related('cctv__site', 'cctv__lokasi')
        .only('pk', 'jenis', 'status', 'diubah', 'cctv__no_cctv', 'cctv__site__kode', 'cctv__lokasi__nama',
              'cctv__lokasi_pemasangan')[:QUEUE_LIMIT]
    )

    selected = None
    wanted = request.GET.get('id', '')
    if wanted.isdigit():
        selected = visible.filter(pk=int(wanted)).first()
    if selected is None and queue:
        selected = queue[0]

    context = {
        'tabs': [(tab, Status(tab).label, counts[tab]) for tab in TABS],
        'status': status,
        'queue': queue,
        'scope': scope,
    }
    if selected:
        selected = (
            PengajuanCoverage.objects
            .select_related('cctv__site', 'cctv__spec', 'cctv__lokasi', 'orthophoto', 'digitasi_oleh',
                            'diajukan_oleh')
            .get(pk=selected.pk)
        )
        tahap = selected.tahap
        kalibrasi_kamera = coverage.active_kalibrasi(selected.cctv)
        if coverage.analysis_is_stale(selected, kalibrasi_kamera):
            # Polygon redrawn (or a newer calibration) since the last check: redo it.
            try:
                coverage.analyse_pengajuan(selected, kalibrasi_kamera)
            except homography.CalibrationError as exc:
                messages.warning(request, f'Analisis homography gagal: {exc}')
        context.update({
            'p': selected,
            'riwayat': selected.riwayat.select_related('user'),
            'checklist_sgi': [(k, label, k in selected.checklist_sgi) for k, label in selected.CHECKLIST_SGI],
            'checklist_safety': [
                (k, label, k in selected.checklist_safety) for k, label in selected.CHECKLIST_SAFETY
            ],
            'tahap': tahap,
            **_stage_marks(selected),
            'can_review': scope.can_review(tahap),
            'can_digitize': scope.can_digitize and selected.status == Status.MENUNGGU_DIGITASI,
            'can_revise': selected.status == Status.DIKEMBALIKAN and scope.can_edit(selected.cctv),
            'can_calibrate': _can_calibrate(scope, selected),
            'can_upload': _can_upload(scope, selected),
            'kalibrasi_kamera': kalibrasi_kamera,
            'analisis': selected.analisis or None,
            **_comparison(selected),
        })
    return render(request, 'cctv/coverage/index.html', context)


def _form_page(request, form, pengajuan=None):
    cctv = form.cctv
    context = {'form': form, 'pengajuan': pengajuan, 'cctv': cctv, 'peta': None}
    if cctv:
        active, versi = coverage.active_coverage(cctv)
        context.update({
            'versi_aktif': versi,
            'peta': {
                'aktif': _feature(active),
                'kamera': list(cctv.posisi.coords) if cctv.posisi else None,
            },
        })
    return render(request, 'cctv/coverage/form.html', context)


def _current_orthophoto():
    return Orthophoto.objects.filter(aktif=True).first()


@login_required
def create(request):
    scope = _scope(request.user)
    if not scope.can_create:
        raise PermissionDenied
    form = PengajuanForm(request.POST or None, request.FILES or None, scope=scope)
    if request.method == 'GET':
        # ?cctv=<pk> from a CCTV page, or ?no=<No. CCTV> from the picker: the map then
        # shows the camera and its active coverage.
        editable = scope.filter(Cctv.objects.all(), edit=True)
        wanted = request.GET.get('cctv', '')
        if wanted.isdigit():
            form.cctv = editable.filter(pk=int(wanted)).first()
        elif request.GET.get('no'):
            form.cctv = editable.filter(no_cctv__iexact=request.GET['no'].strip()).first()
        if form.cctv:
            form.fields['no_cctv'].initial = form.cctv.no_cctv
    if request.method == 'POST' and form.is_valid():
        pengajuan = coverage.submit(form.save(commit=False), request.user, _current_orthophoto())
        messages.success(request, f'Pengajuan coverage {pengajuan.cctv.no_cctv} dikirim ke SGI.')
        return redirect(f"{reverse('cctv:coverage_index')}?status={pengajuan.status}&id={pengajuan.pk}")
    return _form_page(request, form)


@login_required
def revise(request, pk):
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope).select_related('cctv__spec'), pk=pk)
    if pengajuan.status != Status.DIKEMBALIKAN or not scope.can_edit(pengajuan.cctv):
        raise PermissionDenied
    form = PengajuanForm(request.POST or None, request.FILES or None, instance=pengajuan, scope=scope)
    if request.method == 'POST' and form.is_valid():
        coverage.resubmit(form.save(commit=False), request.user, _current_orthophoto())
        messages.success(request, f'Revisi {pengajuan.cctv.no_cctv} dikirim ulang ke SGI.')
        return redirect(f"{reverse('cctv:coverage_index')}?status={Status.MENUNGGU_SGI}&id={pengajuan.pk}")
    return _form_page(request, form, pengajuan)


@login_required
@require_POST
def review(request, pk):
    scope = _scope(request.user)
    pengajuan = get_object_or_404(PengajuanCoverage.objects.select_related('cctv'), pk=pk)
    if not scope.can_review(pengajuan.tahap):
        raise PermissionDenied  # SGI stage: sgi role; Safety stage: safety-approval role (admins: both)
    form = ReviewForm(request.POST)
    back = f"{reverse('cctv:coverage_index')}?status={pengajuan.status}&id={pengajuan.pk}"
    if not form.is_valid():
        messages.error(request, 'Data review tidak valid.')
        return redirect(back)
    try:
        coverage.review(
            pengajuan, request.user,
            setujui=form.cleaned_data['keputusan'] == 'setujui',
            checklist=form.cleaned_data['checklist'],
            catatan=form.cleaned_data['catatan'],
        )
    except coverage.ReviewError as exc:
        messages.error(request, exc.message)
        return redirect(back)
    no = pengajuan.cctv.no_cctv
    messages.success(request, {
        Status.MENUNGGU_SAFETY: f'{no} disetujui SGI dan diteruskan ke Safety.',
        Status.SELESAI: f'{no} disetujui Safety. Coverage v{pengajuan.versi} sekarang aktif.',
        Status.DIKEMBALIKAN: f'{no} dikembalikan ke pengaju.',
        Status.MENUNGGU_DIGITASI: f'{no} dikembalikan ke SGI untuk digitasi ulang.',
    }[pengajuan.status])
    return redirect(f"{reverse('cctv:coverage_index')}?status={pengajuan.status}&id={pengajuan.pk}")


def _can_calibrate(scope, pengajuan):
    """Safety calibrates the camera (admins too), while the request is still open."""
    return pengajuan.status != Status.SELESAI and (scope.full or scope.is_safety)


def _can_upload(scope, pengajuan):
    return pengajuan.status != Status.SELESAI and (
        _can_calibrate(scope, pengajuan) or scope.is_sgi or scope.can_edit(pengajuan.cctv))


@login_required
@require_POST
def kalibrasi(request, pk):
    """Save the camera calibration (JSON: {gcp: [{px, py, lon, lat}], lebar, tinggi}) and analyse."""
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope).select_related('cctv__spec'), pk=pk)
    if not _can_calibrate(scope, pengajuan):
        raise PermissionDenied
    try:
        kalibrasi = coverage.save_kalibrasi(pengajuan, request.user, json.loads(request.body))
    except coverage.GeometryError as exc:  # a ValueError too, so it goes first
        return JsonResponse({'detail': str(exc)}, status=400)
    except ValueError:
        return JsonResponse({'detail': 'JSON tidak valid.'}, status=400)
    return JsonResponse({'gcp': len(kalibrasi.gcp), 'hasil': kalibrasi.hasil, 'analisis': pengajuan.analisis})


@login_required
@require_POST
def upload_tangkapan(request, pk):
    """Attach (or replace) the CCTV screenshot that calibration and evidence are drawn on."""
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope).select_related('cctv__spec'), pk=pk)
    if not _can_upload(scope, pengajuan):
        raise PermissionDenied
    back = f"{reverse('cctv:coverage_index')}?status={pengajuan.status}&id={pengajuan.pk}"
    upload = request.FILES.get('tangkapan_layar')
    if not upload or upload.size > SCREENSHOT_MAX_MB * 1024 * 1024 or not is_image(upload):
        messages.error(request, f'Pilih gambar PNG, JPG, atau WEBP (maks. {SCREENSHOT_MAX_MB} MB).')
        return redirect(back)
    if pengajuan.tangkapan_layar:
        pengajuan.tangkapan_layar.delete(save=False)
    pengajuan.tangkapan_layar = upload
    pengajuan.analisis = {}
    pengajuan.save(update_fields=['tangkapan_layar', 'analisis', 'diubah'])
    messages.success(request, 'Tangkapan layar disimpan.')
    return redirect(back)


def _serve(field):
    if not field:
        raise Http404
    response = FileResponse(field.open('rb'))
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@login_required
def tangkapan(request, pk):
    """The screenshot, served only to users who may see the request (not from /media/)."""
    return _serve(get_object_or_404(_visible(_scope(request.user)), pk=pk).tangkapan_layar)


@login_required
def bukti(request, pk):
    """The screenshot with the coverage drawn on (evidence of the homography check)."""
    return _serve(get_object_or_404(_visible(_scope(request.user)), pk=pk).bukti)
