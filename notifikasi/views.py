"""Master PIC of the change request stages and the notification log (Pengaturan > Master PIC)."""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, Q, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.karyawan_sync import karyawan_by_sid
from core.models import Karyawan
from navigation import roles

from . import services
from .forms import PicForm
from .models import Notifikasi, Pic

MANAGERS = (roles.ADMIN_HAZARD_MOTION,)
LOG_LIMIT = 50


def _check_manager(user):
    if not roles.has_any_role(user, roles.user_group_names(user), MANAGERS):
        raise PermissionDenied


def _pic_page(tahap):
    """Back to the master PIC page, on the tab of this stage."""
    return redirect(f"{reverse('notifikasi:pic_list')}?tab={tahap}")


def _report(request, rows, what):
    if not rows:
        messages.warning(request, f'{what}: PIC ini belum punya nomor WA atau email.')
        return
    counts = services.status_counts(rows)
    level = messages.warning if counts['gagal'] else messages.success
    level(request, f"{what}: {counts['terkirim']} terkirim, {counts['gagal']} gagal, "
                   f"{counts['menunggu']} di antrean. Lihat log di bawah untuk detailnya.")


@login_required
def pic_list(request):
    _check_manager(request.user)
    # A refused assignment stays on the stage it was for, so the form and its list stay together.
    tab = request.POST.get('tahap') or request.GET.get('tab')
    if tab not in Pic.Tahap.values:
        tab = Pic.Tahap.IT.value
    form = PicForm(request.POST or None, initial={'tahap': tab})
    if request.method == 'POST' and form.is_valid():
        pic = form.save()
        messages.success(request, f'PIC {pic.nama} ditambahkan.')
        return _pic_page(pic.tahap)
    pics = list(Pic.objects.select_related('site', 'perusahaan', 'karyawan'))
    return render(request, 'notifikasi/pic_list.html', {
        'form': form,
        'tab': tab,
        'tabs': [(value, label, sum(pic.tahap == value for pic in pics)) for value, label in Pic.Tahap.choices],
        'tab_label': Pic.Tahap(tab).label,
        'pics': [pic for pic in pics if pic.tahap == tab],
        'log': Notifikasi.objects.select_related('pic', 'cctv')[:LOG_LIMIT],
    })


@login_required
def pic_update(request, pk):
    _check_manager(request.user)
    pic = get_object_or_404(Pic, pk=pk)
    form = PicForm(request.POST or None, instance=pic)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'PIC {pic.nama} disimpan.')
        return _pic_page(pic.tahap)
    return render(request, 'notifikasi/pic_form.html', {'form': form, 'pic': pic})


SEARCH_LIMIT = 10
SID_PATTERN = re.compile(r'^[A-Z0-9]{4,10}$')


def _karyawan_json(k):
    return {
        'sid': k.sid, 'nama': k.nama, 'email': k.email, 'no_hp': k.no_hp,
        'perusahaan': k.perusahaan.nama if k.perusahaan_id else '',
        'jabatan': k.jabatan_struktural, 'site': k.site, 'status': k.status,
    }


@login_required
def cari_karyawan(request):
    """Autocomplete for the PIC form: synced employees by SID or name, else the SID API by SID."""
    _check_manager(request.user)
    q = request.GET.get('q', '').strip()[:50]
    if len(q) < 2:
        return JsonResponse({'results': []})
    matches = list(
        Karyawan.objects.select_related('perusahaan')
        .filter(Q(sid__istartswith=q) | Q(nama__icontains=q))
        .order_by(Case(When(status='AKTIF', then=0), default=1), 'nama')[:SEARCH_LIMIT]
    )
    if not matches and SID_PATTERN.match(q.upper()):
        found = karyawan_by_sid(q)  # not synced yet: ask the SID API directly
        matches = [found] if found else []
    return JsonResponse({'results': [_karyawan_json(k) for k in matches]})


@login_required
@require_POST
def pic_delete(request, pk):
    _check_manager(request.user)
    pic = get_object_or_404(Pic, pk=pk)
    pic.delete()  # its log rows stay, with the PIC set to empty
    messages.success(request, f'PIC {pic.nama} dihapus.')
    return _pic_page(pic.tahap)


@login_required
@require_POST
def pic_test(request, pk):
    _check_manager(request.user)
    pic = get_object_or_404(Pic, pk=pk)
    with transaction.atomic():
        rows = services.notify_test(pic, request.user)
    _report(request, rows, f'Tes ke {pic.nama}')
    return _pic_page(pic.tahap)


@login_required
@require_POST
def resend(request, pk):
    _check_manager(request.user)
    notifikasi = get_object_or_404(Notifikasi, pk=pk)
    services.resend([notifikasi])
    _report(request, [notifikasi], 'Kirim ulang')
    return redirect('notifikasi:pic_list')
