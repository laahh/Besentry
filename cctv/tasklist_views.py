"""Tasklist: the open requests waiting for the signed-in user, next to the progress of one. It has
two lists, CCTV coverage (here) and work areas (areakerja.tasklist), each with the same shape.

A task belongs to the same people a notification goes to: the PICs of the stage covering the CCTV,
or the holders of the stage's role when no PIC covers it. IT also keeps their own requests, and a
draft belongs to its author alone. Admins see every task, to follow up on any PIC.

The counts and stage times above the list cover every request the user may open, so they stay the
same when an admin switches between "Semua PIC" and "Tugas saya".
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from areakerja import tasklist as area_tasklist
from navigation import roles
from notifikasi.models import Pic

from .access import get_scope
from .models import PengajuanCoverage, RiwayatPengajuan
from .pengajuan_views import TABS, _can_it, _can_mitra, _can_safety, _can_sgi, _scope, _tabs, _visible
from .sla import RETURNED, TRANSITIONS, format_durasi, stage_summary, stage_times
from .tasklist_common import build_groups, build_statistik, covering, owned_by, pick_selected

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
# PT Berau Coal: Survey BC digitises and verifies in one step.
TUGAS_GABUNG = {
    Status.MENUNGGU_SGI: 'Digitasi dan verifikasi boundary coverage',
    Status.RETURN_SGI: 'Perbaiki boundary yang dikembalikan Safety',
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
WORK_STAGES = [(key, label) for key, label in TABS if key != 'selesai']
JENIS = ('cctv', 'area')
JENIS_LABEL = {'cctv': 'Coverage CCTV', 'area': 'Area Kerja'}
BELL_LISTED = 8  # tasks named in the header's dropdown; the rest are on the Tasklist


def _covering(pics, tahap, pengajuan):
    return covering(pics, tahap, pengajuan.cctv) if pengajuan.cctv_id else []


def _is_mine(pengajuan, user, group_names, pics):
    tahap = pengajuan.tahap
    if tahap == 'it' and pengajuan.diajukan_oleh_id == user.pk:
        return True
    if pengajuan.status == Status.DRAFT_IT or not pengajuan.cctv_id:
        return False
    return owned_by(tahap, STAGE_ROLE.get(tahap), pengajuan.cctv, user, group_names, pics)


def _tugas(pengajuan):
    return (pengajuan.gabung and TUGAS_GABUNG.get(pengajuan.status)) or TUGAS[pengajuan.status]


def _progress(pengajuan, pics, spent):
    """The stages of one request, for the vertical stepper."""
    stages = _tabs(pengajuan)
    reached = [key for key, _ in stages].index(pengajuan.tahap)
    closed, returned = {}, None
    for entry in pengajuan.riwayat.select_related('user'):  # oldest first: the latest one wins
        if entry.aksi in STAGE_DONE_BY:
            closed[STAGE_DONE_BY[entry.aksi]] = entry
        elif entry.aksi == Aksi.DIKEMBALIKAN:
            returned = entry
    steps = []
    for index, (key, label) in enumerate(stages):
        is_current = index == reached
        steps.append({
            'label': label,
            'nomor': index + 1,
            'selesai': index < reached,
            'aktif': is_current,
            'dikembalikan': is_current and pengajuan.status in RETURNED,
            'tugas': _tugas(pengajuan) if is_current else '',
            'catatan': returned.catatan if is_current and returned and pengajuan.status in RETURNED else '',
            'oleh': closed.get(key) if index < reached else None,
            'pics': [pic.nama for pic in _covering(pics, key, pengajuan)],
            'punya_pic': key != 'selesai',
            'durasi': format_durasi(spent[key]) if key in spent else '',
        })
    return steps


def _lokasi(pengajuan):
    return f'{pengajuan.cctv.site.kode} · {pengajuan.cctv.perusahaan.nama}' if pengajuan.cctv_id else ''


def _row(pengajuan, pics, spent):
    waiting = spent[pengajuan.tahap]
    return {
        'p': pengajuan,
        'judul': pengajuan.no_cctv,
        'status': pengajuan.get_status_display(),
        'badge': pengajuan.badge,
        'lokasi': _lokasi(pengajuan),
        'minggu': pengajuan.minggu_label if pengajuan.minggu else '',
        'url': reverse('cctv:pengajuan', args=[pengajuan.pk]),
        'tugas': _tugas(pengajuan),
        'pics': [pic.nama for pic in _covering(pics, pengajuan.tahap, pengajuan)],
        'menunggu': waiting,
        'di_tahap': format_durasi(waiting),
    }


def _load_requests(scope, user, only_open=False):
    """Every request the user may count, with what the stage times need. Others' drafts stay private."""
    transitions = RiwayatPengajuan.objects.filter(aksi__in=TRANSITIONS).only('pengajuan_id', 'aksi', 'catatan', 'waktu')
    requests = (
        _visible(scope, user).select_related(None).select_related('cctv__site', 'cctv__perusahaan')
        .defer('geom', 'geom_sebelum', 'posisi_sebelum', 'cctv__posisi')
        .prefetch_related(Prefetch('riwayat', queryset=transitions, to_attr='transisi'))
        .order_by('diubah')  # waiting longest first
    )
    if only_open:
        requests = requests.exclude(status__in=PengajuanCoverage.SELESAI)
    return [p for p in requests
            if scope.full or p.status != Status.DRAFT_IT or p.diajukan_oleh_id == user.pk]


def _mine(requests, scope, user, group_names, pics):
    return [p for p in requests if p.terbuka and CAN_WORK[p.tahap](scope, p)
            and _is_mine(p, user, group_names, pics)]


def _task_count(scope, user, group_names, pics, show_all):
    open_requests = _load_requests(scope, user, only_open=True)
    return len(open_requests if show_all else _mine(open_requests, scope, user, group_names, pics))


def _my_rows(scope, user, group_names, pics):
    requests = _load_requests(scope, user, only_open=True)
    now = timezone.now()
    return [_row(p, pics, stage_times(p, p.transisi, now)) for p in _mine(requests, scope, user, group_names, pics)]


def _short_name(pengajuan):
    return pengajuan.no_cctv


def _context(scope, user, group_names, pics, show_all, requested_pk):
    requests = _load_requests(scope, user)
    now = timezone.now()
    times = {p.pk: stage_times(p, p.transisi, now) for p in requests}
    mine = _mine(requests, scope, user, group_names, pics)
    tasks = [p for p in requests if p.terbuka] if show_all else mine
    rows = [_row(p, pics, times[p.pk]) for p in tasks]
    selected = pick_selected(rows, TABS, requested_pk)
    warnings = sum(p.status == Status.APPROVED_WARNING for p in requests)
    return {
        'rows': rows,
        'groups': build_groups(rows, TABS, selected),
        'statistik': build_statistik(requests, times, mine, RETURNED, warnings, _short_name),
        'sla_tahap': stage_summary([(p, times[p.pk]) for p in requests], WORK_STAGES),
        'selected': selected,
        'selected_judul': f'{selected.no_cctv} · {selected.get_jenis_display()}' if selected else '',
        'steps': _progress(selected, pics, times[selected.pk]) if selected else [],
    }


def _choose_list(asked, counts):
    if asked in JENIS:
        return asked
    return 'area' if counts['area'] and not counts['cctv'] else 'cctv'


@login_required
def tasklist(request):
    scope = _scope(request.user)
    user = request.user
    pics = list(Pic.objects.filter(aktif=True).select_related('karyawan'))
    group_names = roles.user_group_names(user)
    show_all = scope.full and request.GET.get('lihat') != 'saya'
    args = (scope, user, group_names, pics, show_all)
    counts = {'cctv': _task_count(*args), 'area': area_tasklist.task_count(*args)}
    jenis = _choose_list(request.GET.get('jenis'), counts)
    build = area_tasklist.context if jenis == 'area' else _context
    return render(request, 'cctv/tasklist.html', {
        **build(*args, request.GET.get('p')),
        'jenis': jenis,
        'jumlah_tugas': counts,
        'can_upload_many': scope.full or scope.is_mitra,
        'can_see_all': scope.full,
        'show_all': show_all,
    })


def _bell_rows(scope, user):
    """Everyone's own tasks of both lists, newest first; an admin's are only those they hold themselves."""
    pics = list(Pic.objects.filter(aktif=True).select_related('karyawan'))
    group_names = roles.user_group_names(user)
    sources = (('cctv', _my_rows), ('area', area_tasklist.my_rows))
    rows = []
    for jenis, source in sources:
        for row in source(scope, user, group_names, pics):
            rows.append({**row, 'daftar': JENIS_LABEL[jenis],
                         'tautan': f"{reverse('cctv:tasklist')}?jenis={jenis}&p={row['p'].pk}"})
    return sorted(rows, key=lambda row: row['p'].diubah, reverse=True)


@login_required
@never_cache
def lonceng(request):
    """The dropdown of the bell in the header, asked for by static/js/lonceng.js after a page loads."""
    scope = get_scope(request.user)
    rows = _bell_rows(scope, request.user) if scope.can_open else []
    return render(request, 'cctv/partials/lonceng.html', {
        'rows': rows[:BELL_LISTED],
        'total': len(rows),
        'can_open': scope.can_open,
    })
