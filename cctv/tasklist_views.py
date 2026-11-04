"""Tasklist: the open change requests waiting for the signed-in user, next to the progress of one.

A task belongs to the same people a notification goes to: the PICs of the stage covering the CCTV,
or the holders of the stage's role when no PIC covers it. IT also keeps their own requests, and a
draft belongs to its author alone. Admins see every task, to follow up on any PIC.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from navigation import roles
from notifikasi.models import Pic

from .models import PengajuanCoverage, RiwayatPengajuan
from .pengajuan_views import TABS, _can_it, _can_mitra, _can_safety, _can_sgi, _scope, _visible

Status = PengajuanCoverage.Status
Aksi = RiwayatPengajuan.Aksi

TUGAS = {
    Status.DRAFT_IT: 'Lengkapi draft, lalu lanjutkan ke digitasi',
    Status.RETURN_IT: 'Perbaiki data CCTV yang dikembalikan',
    Status.MENUNGGU_MITRA: 'Digitasi boundary coverage',
    Status.RETURN_MITRA: 'Perbaiki boundary yang dikembalikan',
    Status.MENUNGGU_SGI: 'Review boundary coverage',
    Status.RETURN_SGI: 'Review ulang boundary yang dikembalikan Safety',
    Status.MENUNGGU_SAFETY: 'Review dan beri keputusan Safety',
}
CAN_WORK = {'it': _can_it, 'mitra': _can_mitra, 'sgi': _can_sgi, 'safety': _can_safety}
STAGE_ROLE = {'mitra': roles.SURVEY_MITRA, 'sgi': roles.SURVEY_BC, 'safety': roles.SAFETY}
# The history entry that closes each stage.
STAGE_DONE_BY = {
    Aksi.DIAJUKAN: 'it',
    Aksi.DIGITASI: 'mitra',
    Aksi.DISETUJUI_SGI: 'sgi',
    Aksi.DISETUJUI_SAFETY: 'safety',
    Aksi.DISETUJUI_WARNING: 'safety',
}
RETURNED = (Status.RETURN_IT, Status.RETURN_MITRA, Status.RETURN_SGI)


def _covering(pics, tahap, pengajuan):
    if not pengajuan.cctv_id:
        return []
    return [pic for pic in pics if pic.tahap == tahap and pic.covers(pengajuan.cctv)]


def _is_mine(pengajuan, user, group_names, pics):
    tahap = pengajuan.tahap
    if tahap == 'it' and pengajuan.diajukan_oleh_id == user.pk:
        return True
    if pengajuan.status == Status.DRAFT_IT:
        return False
    assigned = _covering(pics, tahap, pengajuan)
    if assigned:
        return any(pic.karyawan and pic.karyawan.user_id == user.pk for pic in assigned)
    # The role itself, not scope.is_*: being PIC of another site must not bring this one along.
    return STAGE_ROLE.get(tahap) in group_names


def _progress(pengajuan, pics):
    """The five stages of one request, for the vertical stepper."""
    reached = [key for key, _ in TABS].index(pengajuan.tahap)
    closed, returned = {}, None
    for entry in pengajuan.riwayat.select_related('user'):  # oldest first: the latest one wins
        if entry.aksi in STAGE_DONE_BY:
            closed[STAGE_DONE_BY[entry.aksi]] = entry
        elif entry.aksi == Aksi.DIKEMBALIKAN:
            returned = entry
    steps = []
    for index, (key, label) in enumerate(TABS):
        is_current = index == reached
        steps.append({
            'label': label,
            'nomor': index + 1,
            'selesai': index < reached,
            'aktif': is_current,
            'dikembalikan': is_current and pengajuan.status in RETURNED,
            'tugas': TUGAS.get(pengajuan.status) if is_current else '',
            'catatan': returned.catatan if is_current and returned and pengajuan.status in RETURNED else '',
            'oleh': closed.get(key) if index < reached else None,
            'pics': [pic.nama for pic in _covering(pics, key, pengajuan)],
            'punya_pic': key != 'selesai',
        })
    return steps


def _row(pengajuan, pics):
    return {
        'p': pengajuan,
        'tugas': TUGAS[pengajuan.status],
        'pics': [pic.nama for pic in _covering(pics, pengajuan.tahap, pengajuan)],
    }


def _groups(rows, selected):
    """The tasks per stage, in flow order; only the stage of the selected task starts open."""
    groups = []
    for key, label in TABS:
        stage_rows = [row for row in rows if row['p'].tahap == key]
        if stage_rows:
            groups.append({
                'key': key,
                'label': label,
                'rows': stage_rows,
                'buka': selected is not None and selected.tahap == key,
            })
    return groups


@login_required
def tasklist(request):
    scope = _scope(request.user)
    pics = list(Pic.objects.filter(aktif=True).select_related('karyawan'))
    requests = (
        _visible(scope, request.user).select_related(None).select_related('cctv__site', 'cctv__perusahaan')
        .exclude(status__in=PengajuanCoverage.SELESAI)
        .defer('geom', 'geom_sebelum', 'posisi_sebelum', 'cctv__posisi')
        .order_by('diubah')  # waiting longest first
    )
    show_all = scope.full and request.GET.get('lihat') != 'saya'
    group_names = roles.user_group_names(request.user)
    tasks = [
        pengajuan for pengajuan in requests
        if show_all or (CAN_WORK[pengajuan.tahap](scope, pengajuan)
                        and _is_mine(pengajuan, request.user, group_names, pics))
    ]

    rows = [_row(pengajuan, pics) for pengajuan in tasks]
    selected = next((row['p'] for row in rows if str(row['p'].pk) == request.GET.get('p')), None)
    if selected is None and rows:
        selected = _groups(rows, None)[0]['rows'][0]['p']
    return render(request, 'cctv/tasklist.html', {
        'rows': rows,
        'groups': _groups(rows, selected),
        'can_upload_many': scope.full or scope.is_mitra,
        'selected': selected,
        'steps': _progress(selected, pics) if selected else [],
        'can_see_all': scope.full,
        'show_all': show_all,
    })
