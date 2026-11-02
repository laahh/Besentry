"""Spasial > Digitasi Coverage: SGI's queue of CCTV that IT added or moved, to draw their coverage."""

import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from orthophoto.models import Orthophoto

from . import coverage
from .access import get_scope
from .models import PengajuanCoverage, RiwayatPengajuan

Status = PengajuanCoverage.Status
QUEUE_LIMIT = 100
TABS = (
    ('antrian', 'Perlu digitasi', Status.MENUNGGU_DIGITASI),
    ('dikirim', 'Dikirim ke Safety', Status.MENUNGGU_SAFETY),
)


class DigitasiForm(forms.Form):
    geom_json = forms.CharField(required=False, widget=forms.HiddenInput)
    catatan = forms.CharField(required=False, max_length=1000,
                              widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}))
    aksi = forms.ChoiceField(choices=(('draft', 'Simpan draft'), ('kirim', 'Kirim ke Safety')))


def _scope(user):
    scope = get_scope(user)
    if not scope.can_digitize:
        raise PermissionDenied  # sgi role or admin
    return scope


def _feature(geom):
    return json.loads(geom.geojson) if geom else None


@login_required
def index(request):
    _scope(request.user)
    tab = request.GET.get('tab', 'antrian')
    status = dict((key, value) for key, _, value in TABS).get(tab, Status.MENUNGGU_DIGITASI)
    base = PengajuanCoverage.objects.select_related('cctv__site', 'cctv__lokasi')
    counts = {key: base.filter(status=value).count() for key, _, value in TABS}
    queue = base.filter(status=status)
    if status == Status.MENUNGGU_SAFETY:
        queue = queue.filter(digitasi_pada__isnull=False)
    queue = list(queue.order_by('dibuat' if status == Status.MENUNGGU_DIGITASI else '-digitasi_pada')[:QUEUE_LIMIT])

    selected = None
    wanted = request.GET.get('id', '')
    if wanted.isdigit():
        selected = base.select_related('cctv__perusahaan', 'cctv__spec', 'cctv__detail_lokasi', 'diajukan_oleh',
                                       'digitasi_oleh').filter(pk=int(wanted)).first()
    if selected is None and queue:
        selected = base.select_related('cctv__perusahaan', 'cctv__spec', 'cctv__detail_lokasi', 'diajukan_oleh',
                                       'digitasi_oleh').get(pk=queue[0].pk)

    context = {'tabs': [(key, label, counts[key]) for key, label, _ in TABS], 'tab': tab, 'queue': queue}
    if selected:
        active, versi = coverage.active_coverage(selected.cctv)
        context.update({
            'p': selected,
            'versi_aktif': versi,
            'form': DigitasiForm(initial={'geom_json': selected.geom.geojson if selected.geom else ''}),
            'riwayat': selected.riwayat.select_related('user'),
            'perubahan': selected.riwayat.filter(aksi=RiwayatPengajuan.Aksi.DIAJUKAN).order_by('-waktu').first(),
            'bisa_kerja': selected.status == Status.MENUNGGU_DIGITASI,
            'peta': {
                'aktif': _feature(active),
                'kamera': list(selected.cctv.posisi.coords) if selected.cctv.posisi else None,
            },
        })
    return render(request, 'cctv/digitasi/index.html', context)


@login_required
@require_POST
def simpan(request, pk):
    _scope(request.user)
    pengajuan = get_object_or_404(PengajuanCoverage.objects.select_related('cctv__spec'), pk=pk)
    back = f"{reverse('cctv:digitasi_index')}?id={pk}"
    form = DigitasiForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Data digitasi tidak valid.')
        return redirect(back)
    kirim = form.cleaned_data['aksi'] == 'kirim'
    try:
        geom = coverage.parse_geometry(form.cleaned_data['geom_json'])
        coverage.save_digitasi(pengajuan, request.user, geom, kirim=kirim, catatan=form.cleaned_data['catatan'],
                               orthophoto=Orthophoto.objects.filter(aktif=True).first())
    except (coverage.GeometryError, coverage.ReviewError) as exc:
        messages.error(request, getattr(exc, 'message', None) or str(exc))
        return redirect(back)
    if kirim:
        messages.success(request, f'Coverage {pengajuan.cctv.no_cctv} dikirim ke Safety untuk diverifikasi.')
        return redirect('cctv:digitasi_index')
    messages.success(request, f'Draft coverage {pengajuan.cctv.no_cctv} disimpan.')
    return redirect(back)
