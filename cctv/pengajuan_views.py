"""The change request page: one CCTV, five tabs (IT, Survey Mitra, Survey BC, Safety BC, done).

Each tab is editable only for its role while the request sits at that stage; the others are read-only.
"""

import json
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.gis.geos import MultiPolygon
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from notifikasi import services as notifikasi
from orthophoto.models import Orthophoto

from . import coverage
from .access import get_scope
from .forms import CctvAksesForm, CctvForm, CctvSpecForm
from .models import Cctv, PengajuanCoverage
from .pengajuan_forms import CekGeometriForm, DigitasiForm, ItForm, ReviewForm, draft_data, mark_previous
from .views import _akses

Status = PengajuanCoverage.Status
TABS = (
    ('it', 'Input IT'),
    ('mitra', 'Digitasi Survey Mitra'),
    ('sgi', 'Review Survey BC'),
    ('safety', 'Review Safety BC'),
    ('selesai', 'Sah & berlaku'),
)
TAB_KEYS = [key for key, _ in TABS]


def _scope(user):
    scope = get_scope(user)
    if not scope.can_open:
        raise PermissionDenied
    return scope


def _visible(scope, user):
    """Requests the user may open: their CCTV scope, plus their own drafts of new CCTV."""
    queryset = PengajuanCoverage.objects.select_related(
        'cctv__site', 'cctv__perusahaan', 'cctv__spec', 'cctv__lokasi', 'cctv__detail_lokasi',
        'diajukan_oleh', 'digitasi_oleh', 'orthophoto',
    )
    if scope.full:
        return queryset
    in_scope = scope.filter(queryset.filter(cctv__isnull=False), prefix='cctv__').values('pk')
    return queryset.filter(Q(pk__in=in_scope) | Q(cctv__isnull=True, diajukan_oleh=user))


def _url(pengajuan, tab=None):
    url = reverse('cctv:pengajuan', args=[pengajuan.pk])
    return f'{url}?tab={tab}' if tab else url


def _can_it(scope, pengajuan):
    if pengajuan.status not in (Status.DRAFT_IT, Status.RETURN_IT):
        return False
    return scope.can_edit(pengajuan.cctv) if pengajuan.cctv_id else scope.can_create


def _can_mitra(scope, pengajuan):
    return pengajuan.status in (Status.MENUNGGU_MITRA, Status.RETURN_MITRA) and scope.can_digitize(pengajuan.cctv)


def _can_sgi(scope, pengajuan):
    return pengajuan.status in (Status.MENUNGGU_SGI, Status.RETURN_SGI) and scope.can_review_sgi


def _can_safety(scope, pengajuan):
    return pengajuan.status == Status.MENUNGGU_SAFETY and scope.can_review_safety


def _feature(geom):
    return json.loads(geom.geojson) if geom else None


def _orthophoto_extent():
    ortho = Orthophoto.objects.filter(aktif=True).only('batas').first()
    return list(ortho.batas.extent) if ortho and ortho.batas else None


def _extent(*shapes):
    shapes = [shape for shape in shapes if shape]
    if not shapes:
        return _orthophoto_extent()
    union = shapes[0]
    for shape in shapes[1:]:
        union = union.union(shape)
    return list(union.extent)


def _peta(pengajuan):
    """What the maps need: before (position and approved boundary) and after (new position and Mitra's boundary)."""
    cctv = pengajuan.cctv
    posisi = cctv.posisi if cctv else None
    return {
        'sebelum': {
            'geom': _feature(pengajuan.geom_sebelum),
            'kamera': list(pengajuan.posisi_sebelum.coords) if pengajuan.posisi_sebelum else None,
            'extent': _extent(pengajuan.geom_sebelum, pengajuan.posisi_sebelum),
        },
        'sesudah': {
            'geom': _feature(pengajuan.geom),
            'kamera': list(posisi.coords) if posisi else None,
            'extent': _extent(pengajuan.geom, posisi),
        },
        'ortho_extent': _orthophoto_extent(),
        'cctv': pengajuan.no_cctv,
    }


def _it_forms(scope, pengajuan, data=None, files=None):
    """The CCTV, spec and akses forms of the IT tab, filled from the draft when there is one."""
    cctv = pengajuan.cctv if pengajuan else None
    if data is None and pengajuan and pengajuan.data_draft:
        data = QueryDict(mutable=True)
        for key, values in pengajuan.data_draft.items():
            data.setlist(key, values)
    spec = getattr(cctv, 'spec', None) if cctv else None
    akses = _akses(cctv.pk) if cctv and scope.can_see_akses else None
    form = CctvForm(data, instance=cctv, scope=scope)
    spec_form = CctvSpecForm(data, instance=spec, prefix='spec')
    akses_form = CctvAksesForm(data, instance=akses, prefix='akses') if scope.can_see_akses else None
    # Only while IT is still editing: afterwards the stored values are the new ones.
    if pengajuan is None or pengajuan.status in (Status.DRAFT_IT, Status.RETURN_IT):
        for each in (form, spec_form, akses_form):
            mark_previous(each)
    return {
        'form': form,
        'spec_form': spec_form,
        'akses_form': akses_form,
        'it_form': ItForm(
            data, files, pengajuan=pengajuan,
            initial={'keterangan': pengajuan.keterangan if pengajuan else ''},
        ),
        'ada_password': bool(akses and akses.ada_password),
        'spec': spec,
        'akses': akses,
    }


def _render(request, scope, pengajuan, tab=None, forms=None):
    tab = tab if tab in TAB_KEYS else (pengajuan.tahap if pengajuan else 'it')
    can = {
        'it': _can_it(scope, pengajuan) if pengajuan else scope.can_create,
        'mitra': bool(pengajuan) and _can_mitra(scope, pengajuan),
        'sgi': bool(pengajuan) and _can_sgi(scope, pengajuan),
        'safety': bool(pengajuan) and _can_safety(scope, pengajuan),
    }
    reached = TAB_KEYS.index(pengajuan.tahap) if pengajuan else 0
    pics = {}
    if pengajuan and pengajuan.cctv_id:
        for pic in notifikasi.pics_for(pengajuan.cctv).only('tahap', 'nama'):
            pics.setdefault(pic.tahap, []).append(pic.nama)
    context = {
        'p': pengajuan,
        'tab': tab,
        'tabs': [(key, label, index < reached, index == reached, pics.get(key, []))
                 for index, (key, label) in enumerate(TABS)],
        'tab_terbuka': TAB_KEYS[:reached + 1],
        'can': can,
        'scope': scope,
        **(forms or _it_forms(scope, pengajuan)),
    }
    if pengajuan:
        checked_sgi, checked_safety = set(pengajuan.checklist_sgi), set(pengajuan.checklist_safety)
        context.update({
            'riwayat': pengajuan.riwayat.select_related('user'),
            'peta': _peta(pengajuan),
            'digitasi_form': DigitasiForm(initial={
                'geom_json': pengajuan.geom.geojson if pengajuan.geom else '',
                'nama_shp': pengajuan.file_shp,
            }),
            'sebelumnya': coverage.previous_request(pengajuan),
            'checklist_sgi': [(k, label, k in checked_sgi) for k, label in pengajuan.CHECKLIST_SGI],
            'checklist_safety': [(k, label, k in checked_safety) for k, label in pengajuan.CHECKLIST_SAFETY],
            'luas_sebelum': coverage.luas_ha(pengajuan.geom_sebelum),
            'luas_sesudah': coverage.luas_ha(pengajuan.geom),
            'jangkauan_m': coverage.jangkauan_m(pengajuan.cctv) if pengajuan.cctv_id else None,
        })
    return render(request, 'cctv/pengajuan/page.html', context)


def _notify(request, pengajuan):
    _report_sent(request, notifikasi.notify_pengajuan(pengajuan, request.user), pengajuan.get_status_display())


def _report_sent(request, sent, status):
    if not sent:
        messages.warning(request, f'Belum ada penerima notifikasi {status}: '
                                  'tidak ada PIC atau user berole dengan nomor WA/email. Atur di Master PIC.')
        return
    counts = notifikasi.status_counts(sent)
    level = messages.warning if counts['gagal'] else messages.info
    level(request, f"Notifikasi {status}: {counts['terkirim']} terkirim, "
                   f"{counts['gagal']} gagal, {counts['menunggu']} di antrean.")


@login_required
def page(request, pk):
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope, request.user), pk=pk)
    return _render(request, scope, pengajuan, request.GET.get('tab'))


@login_required
def baru(request):
    """IT tab for a CCTV that does not exist yet."""
    scope = _scope(request.user)
    if not scope.can_create:
        raise PermissionDenied
    if request.method == 'POST':
        return _save_it(request, scope, None)
    return _render(request, scope, None, 'it')


@login_required
@require_POST
def ajukan(request, pk):
    """'Ajukan Perubahan' on a CCTV page: open its request, or start a new Draft IT."""
    scope = _scope(request.user)
    cctv = get_object_or_404(scope.filter(Cctv.objects.all()), pk=pk)
    existing = coverage.open_request(cctv)
    if existing:
        return redirect(_url(existing))
    if not scope.can_edit(cctv):
        raise PermissionDenied
    try:
        pengajuan = PengajuanCoverage.objects.create(
            cctv=cctv, jenis=PengajuanCoverage.Jenis.DIPINDAH, diajukan_oleh=request.user,
        )
    except IntegrityError:  # opened by someone else a moment ago
        return redirect(_url(coverage.open_request(cctv)))
    return redirect(_url(pengajuan, 'it'))


@login_required
@require_POST
def it(request, pk):
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope, request.user), pk=pk)
    if not _can_it(scope, pengajuan):
        raise PermissionDenied
    return _save_it(request, scope, pengajuan)


def _save_it(request, scope, pengajuan):
    # The CCTV as stored, read before the forms touch the instance.
    before = None
    if pengajuan and pengajuan.cctv_id:
        before = coverage.geometry_state(Cctv.objects.select_related('spec').get(pk=pengajuan.cctv_id))
    forms = _it_forms(scope, pengajuan, request.POST, request.FILES)
    it_form = forms['it_form']
    if not it_form.is_valid():
        messages.error(request, 'Periksa isian pengajuan.')
        return _render(request, scope, pengajuan, 'it', forms)

    if it_form.cleaned_data['aksi'] == 'draft':
        if pengajuan is None:
            pengajuan = PengajuanCoverage(jenis=PengajuanCoverage.Jenis.BARU, diajukan_oleh=request.user)
        _attach(pengajuan, it_form)
        coverage.save_draft(pengajuan, request.user, draft_data(request.POST))
        messages.success(request, f'Draft {pengajuan.no_cctv} disimpan.')
        return redirect(_url(pengajuan, 'it'))

    cctv_forms = [f for f in (forms['form'], forms['spec_form'], forms['akses_form']) if f]
    if not all(f.is_valid() for f in cctv_forms):
        messages.error(request, 'Periksa isian data CCTV.')
        return _render(request, scope, pengajuan, 'it', forms)

    with transaction.atomic(), transaction.atomic(using='log'):
        saved = forms['form'].save()
        # Spec/akses rows are only created once something is filled in.
        for related_form, existing in ((forms['spec_form'], forms['spec']), (forms['akses_form'], forms['akses'])):
            if related_form and (existing or related_form.has_changed()):
                related_form.instance.cctv = saved
                related_form.save()
        if pengajuan is None:
            pengajuan = PengajuanCoverage(jenis=PengajuanCoverage.Jenis.BARU, diajukan_oleh=request.user)
        _attach(pengajuan, it_form)
        saved = Cctv.objects.select_related('spec').get(pk=saved.pk)
        coverage.continue_request(pengajuan, request.user, saved, before)
    if pengajuan.status == Status.APPROVED:
        messages.success(request, f'{saved.no_cctv} disimpan. Tidak ada perubahan geometri, jadi langsung Approved.')
        return redirect('cctv:detail', pk=saved.pk)
    messages.success(request, f'{saved.no_cctv} diteruskan ke Survey Mitra untuk digitasi.')
    _notify(request, pengajuan)
    return redirect(_url(pengajuan))


def _attach(pengajuan, it_form):
    pengajuan.keterangan = it_form.cleaned_data['keterangan']
    upload = it_form.cleaned_data.get('tangkapan_layar')
    if upload:
        if pengajuan.tangkapan_layar:
            pengajuan.tangkapan_layar.delete(save=False)
        # A fresh name every time, so the versioned URL (tangkapan_url) changes with the picture.
        upload.name = uuid.uuid4().hex + Path(upload.name).suffix.lower()
        pengajuan.tangkapan_layar = upload


@login_required
@require_POST
def mitra(request, pk):
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope, request.user), pk=pk)
    if not _can_mitra(scope, pengajuan):
        raise PermissionDenied
    back = redirect(_url(pengajuan, 'mitra'))
    form = DigitasiForm(request.POST)
    if not form.is_valid():
        messages.error(request, '; '.join(e for errors in form.errors.values() for e in errors))
        return back
    nama = form.cleaned_data['nama_shp']
    sumber = PengajuanCoverage.SumberGeom.SHP if nama else PengajuanCoverage.SumberGeom.GAMBAR
    kirim = form.cleaned_data['aksi'] == 'kirim'
    try:
        coverage.save_digitasi(
            pengajuan, request.user, coverage.parse_geometry(form.cleaned_data['geom_json']), kirim=kirim,
            sumber=sumber, file_shp=nama, catatan=form.cleaned_data['catatan'],
            orthophoto=Orthophoto.objects.filter(aktif=True).first(),
        )
    except (coverage.GeometryError, coverage.ReviewError) as exc:
        messages.error(request, getattr(exc, 'message', None) or str(exc))
        return back
    if kirim:
        messages.success(request, f'Boundary {pengajuan.no_cctv} diajukan ke Survey BC.')
        _notify(request, pengajuan)
        return redirect(_url(pengajuan))
    messages.success(request, f'Draft boundary {pengajuan.no_cctv} disimpan.')
    return back


@login_required
@require_POST
def mitra_cek(request, pk):
    """Read an SHP into an editable polygon, or check a drawn one; nothing is saved.

    {geom, nama, catatan, valid, pesan} or {error} with status 400.
    """
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope, request.user), pk=pk)
    if not _can_mitra(scope, pengajuan):
        raise PermissionDenied
    form = CekGeometriForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({'error': '; '.join(e for errors in form.errors.values() for e in errors)}, status=400)
    upload = form.cleaned_data['file_shp']
    catatan = ''
    try:
        if upload:
            polygon, catatan = coverage.editable_polygon(coverage.read_shp_zip(upload))
            geom = MultiPolygon(polygon, srid=4326)
        else:
            geom = coverage.parse_geometry(form.cleaned_data['geom_json'])
    except coverage.GeometryError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    try:
        valid, pesan = True, coverage.validate_geometry(geom, pengajuan.cctv)
    except coverage.GeometryError as exc:
        valid, pesan = False, str(exc)
    return JsonResponse({
        'geom': json.loads(geom[0].geojson) if geom else None,
        'nama': upload.name if upload else '',
        'catatan': catatan,
        'valid': valid,
        'pesan': pesan,
    })


def _review(request, pk, can, run):
    scope = _scope(request.user)
    pengajuan = get_object_or_404(_visible(scope, request.user), pk=pk)
    if not can(scope, pengajuan):
        raise PermissionDenied
    back = redirect(_url(pengajuan, pengajuan.tahap))
    form = ReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Data review tidak valid.')
        return back
    try:
        run(pengajuan, request.user, aksi=form.cleaned_data['aksi'], checklist=form.cleaned_data['checklist'],
            catatan=form.cleaned_data['catatan'])
    except coverage.ReviewError as exc:  # nothing was saved
        messages.error(request, exc.message)
        return back
    messages.success(request, f'{pengajuan.no_cctv}: {pengajuan.get_status_display()}.')
    _notify(request, pengajuan)
    return redirect(_url(pengajuan))


@login_required
@require_POST
def sgi(request, pk):
    return _review(request, pk, _can_sgi, coverage.review_sgi)


@login_required
@require_POST
def safety(request, pk):
    return _review(request, pk, _can_safety, coverage.review_safety)


@login_required
def tangkapan(request, pk):
    """The screenshot, served only to users who may see the request (not from /media/)."""
    pengajuan = get_object_or_404(_visible(_scope(request.user), request.user), pk=pk)
    if not pengajuan.tangkapan_layar:
        raise Http404
    response = FileResponse(pengajuan.tangkapan_layar.open('rb'))
    response['Cache-Control'] = 'private, max-age=3600'
    return response
