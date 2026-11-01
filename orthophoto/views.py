from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render

from navigation import roles

from . import jobs, services
from .forms import PublishForm
from .models import Orthophoto

PUBLISHERS = (roles.ADMIN_HAZARD_MOTION,)
RIWAYAT_LIMIT = 12
STATUS_BADGE = {
    Orthophoto.Status.MENUNGGU: 'warning',
    Orthophoto.Status.DIPROSES: 'info',
    Orthophoto.Status.SIAP: 'success',
    Orthophoto.Status.GAGAL: 'danger',
    Orthophoto.Status.DIGANTI: 'light',
}


def _check_publisher(user):
    if not roles.has_any_role(user, roles.user_group_names(user), PUBLISHERS):
        raise PermissionDenied


def _status_row(ortho):
    return {
        'id': ortho.pk,
        'status': ortho.status,
        'status_label': ortho.get_status_display(),
        'badge': STATUS_BADGE[ortho.status],
        'berjalan': ortho.berjalan,
        'progres': ortho.progres,
        'pesan_error': ortho.pesan_error,
    }


@login_required
def publish(request):
    _check_publisher(request.user)
    berjalan = Orthophoto.objects.filter(status__in=Orthophoto.BERJALAN).first()

    form = PublishForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if berjalan:
            messages.error(request, 'Masih ada publish yang berjalan. Tunggu sampai selesai.')
            return redirect('orthophoto:publish')
        ortho = Orthophoto.objects.create(
            bulan=form.cleaned_data['bulan'],
            file_sumber=form.cleaned_data['file_sumber'],
            dibuat_oleh=request.user,
        )
        transaction.on_commit(lambda: jobs.enqueue_publish(ortho))
        messages.success(request, f'Publish orthophoto {ortho.bulan:%Y-%m} masuk antrean.')
        return redirect('orthophoto:publish')

    aktif = Orthophoto.objects.filter(aktif=True).first()
    context = {
        'form': form,
        'aktif': aktif,
        'preview_url': services.preview_url(aktif) if aktif else '',
        'berjalan': berjalan,
        'riwayat': [
            {'ortho': ortho, **_status_row(ortho)}
            for ortho in Orthophoto.objects.select_related('dibuat_oleh')[:RIWAYAT_LIMIT]
        ],
    }
    return render(request, 'orthophoto/publish.html', context)


@login_required
def status(request):
    """Polled by the publish page while a job runs."""
    _check_publisher(request.user)
    rows = Orthophoto.objects.all()[:RIWAYAT_LIMIT]
    return JsonResponse({
        'berjalan': any(ortho.berjalan for ortho in rows),
        'riwayat': [_status_row(ortho) for ortho in rows],
    })


@login_required
def current(request):
    """The current month's orthophoto, as MapLibre needs it for a raster source."""
    ortho = Orthophoto.objects.filter(aktif=True).first()
    if ortho is None:
        return JsonResponse({'detail': 'Belum ada orthophoto aktif.'}, status=404)
    return JsonResponse({
        'bulan': f'{ortho.bulan:%Y-%m}',
        'bounds': list(ortho.batas.extent),
        'minzoom': ortho.zoom_min,
        'maxzoom': ortho.zoom_maks,
        'tile_url': services.tile_url_template(ortho),
        'tile_size': services.TILE_SIZE,
    })
