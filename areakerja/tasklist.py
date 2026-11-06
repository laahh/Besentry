"""The work area list of the Tasklist (see cctv.tasklist_views for the page).

Upload stage: a draft belongs to its author; a request sent back goes to whoever uploads for that
Mitra Site (Survey Mitra, or Survey BC for PT Berau Coal) and to whoever sent it.
Verifikasi BC: SGI, Mining and Safety BC work side by side, so a request is a task for each
department that has not approved it yet.

Requests approved without a person (the first import) count in the cards but not in the average
times, since nobody spent that time on them.
"""

from datetime import timedelta

from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from cctv.coverage import this_monday
from cctv.sla import accumulate, average_durasi, format_durasi, longest_durasi, stage_summary
from cctv.tasklist_common import build_groups, build_statistik, covering, owned_by, pick_selected
from core.templatetags.pengguna import nama_user

from . import access
from .models import Departemen, PengajuanAreaKerja, RiwayatAreaKerja, VerifikasiAreaKerja
from .views import TABS, TABS_GABUNG

Status = PengajuanAreaKerja.Status
Aksi = RiwayatAreaKerja.Aksi

RETURNED = (Status.RETURN_MITRA,)
STAGES = (('pengajuan', 'Upload area kerja'), ('sgi', 'Verifikasi BC'), ('selesai', 'Sah & berlaku'))
WORK_STAGES = STAGES[:2]
TUGAS = {
    Status.DRAFT: 'Lengkapi dan ajukan area kerja',
    Status.RETURN_MITRA: 'Perbaiki area kerja yang dikembalikan',
}
CLOSES = {Aksi.DIAJUKAN: 'pengajuan', Aksi.DIKEMBALIKAN: 'sgi', Aksi.DISETUJUI: 'sgi'}
# The stage table already has 'sgi' for Verifikasi BC as a whole.
DEPARTMENT_ROW_KEY = {Departemen.SGI: 'sgi_dept', Departemen.MINING: 'mining', Departemen.SAFETY: 'safety'}


def _load_requests(scope, user, only_open=False):
    """Every request the user may count, with its history. Others' drafts stay private."""
    transitions = RiwayatAreaKerja.objects.filter(aksi__in=CLOSES).select_related('user').defer('sebelum')
    requests = (
        PengajuanAreaKerja.objects.filter(mitra_site__in=access.visible(scope))
        .select_related('mitra_site__site', 'mitra_site__perusahaan')
        .prefetch_related(
            Prefetch('riwayat', queryset=transitions, to_attr='transisi'),
            Prefetch('verifikasi', queryset=VerifikasiAreaKerja.objects.select_related('user')),
        )
        .order_by('diubah')  # waiting longest first
    )
    if only_open:
        requests = requests.exclude(status=Status.APPROVED)
    return [p for p in requests if scope.full or p.status != Status.DRAFT or p.diajukan_oleh_id == user.pk]


def _pending(pengajuan):
    done = {row.departemen for row in pengajuan.verifikasi.all()}
    return [department for department in Departemen if department.value not in done]


def _is_mine(pengajuan, scope, user, group_names, pics):
    place = pengajuan.mitra_site
    if pengajuan.status == Status.MENUNGGU_SGI:
        allowed = access.departemen(scope, place)
        return any(department.value in allowed
                   and owned_by(*access.DEPARTEMEN_OWNER[department], place, user, group_names, pics)
                   for department in _pending(pengajuan))
    if pengajuan.status == Status.APPROVED or not access.can_upload(scope, place):
        return False
    if pengajuan.diajukan_oleh_id == user.pk:
        return True
    return pengajuan.status != Status.DRAFT and owned_by(*access.uploader(place), place, user, group_names, pics)


def _mine(requests, scope, user, group_names, pics):
    return [p for p in requests if _is_mine(p, scope, user, group_names, pics)]


def task_count(scope, user, group_names, pics, show_all):
    open_requests = _load_requests(scope, user, only_open=True)
    return len(open_requests if show_all else _mine(open_requests, scope, user, group_names, pics))


def my_rows(scope, user, group_names, pics):
    """This user's own open tasks, for the bell in the header."""
    requests = _load_requests(scope, user, only_open=True)
    now = timezone.now()
    return [_row(p, pics, _stage_times(p, now)) for p in _mine(requests, scope, user, group_names, pics)]


def _stage_times(pengajuan, now):
    events = [(CLOSES[entry.aksi], entry.waktu) for entry in pengajuan.transisi]
    return accumulate(pengajuan.dibuat, events, pengajuan.tahap if pengajuan.terbuka else None, now)


def _tugas(pengajuan):
    if pengajuan.status == Status.MENUNGGU_SGI:
        names = ', '.join(department.label for department in _pending(pengajuan))
        return f'Verifikasi area kerja, menunggu {names}'
    return TUGAS[pengajuan.status]


def _department_pics(pics, department, place):
    return [pic.nama for pic in covering(pics, access.DEPARTEMEN_OWNER[department][0], place)]


def _task_pics(pengajuan, pics):
    place = pengajuan.mitra_site
    if pengajuan.status == Status.MENUNGGU_SGI:
        names = [name for department in _pending(pengajuan) for name in _department_pics(pics, department, place)]
    else:
        names = [pic.nama for pic in covering(pics, access.uploader(place)[0], place)]
    return list(dict.fromkeys(names))


def _judul(pengajuan):
    return f'{pengajuan.mitra_site.site.kode} - {pengajuan.mitra_site.perusahaan.nama}'


def _short_name(pengajuan):
    company = pengajuan.mitra_site.perusahaan
    return f'{pengajuan.mitra_site.site.kode} · {company.kode or company.nama}'


def _row(pengajuan, pics, spent):
    waiting = spent[pengajuan.tahap]
    return {
        'p': pengajuan,
        'judul': _judul(pengajuan),
        'status': pengajuan.status_label,
        'badge': pengajuan.badge,
        'lokasi': '',
        'minggu': pengajuan.minggu_label,
        'url': reverse('areakerja:pengajuan', args=[pengajuan.pk]),
        'tugas': _tugas(pengajuan),
        'pics': _task_pics(pengajuan, pics),
        'menunggu': waiting,
        'di_tahap': format_durasi(waiting),
    }


def _wait(start, end):
    return max(end - start, timedelta())


def _department_lines(pengajuan, pics, now):
    done = {row.departemen: row for row in pengajuan.verifikasi.all()}
    lines = []
    for department in Departemen:
        row = done.get(department.value)
        if row:
            line = (f'{department.label}: disetujui {nama_user(row.user, "Sistem")}, '
                    f'{timezone.localtime(row.waktu):%d %b %H:%M} · '
                    f'{format_durasi(_wait(pengajuan.diajukan_pada, row.waktu))}')
        else:
            names = _department_pics(pics, department, pengajuan.mitra_site)
            line = f'{department.label}: menunggu {format_durasi(_wait(pengajuan.diajukan_pada, now))}'
            line += f' · PIC: {", ".join(names)}' if names else ''
        lines.append(line)
    return lines


def _progress(pengajuan, pics, spent, now):
    """The stages of one request, for the vertical stepper."""
    stages = TABS_GABUNG if pengajuan.gabung else TABS
    reached = [key for key, _ in stages].index(pengajuan.tahap)
    is_returned = pengajuan.status == Status.RETURN_MITRA
    latest = {entry.aksi: entry for entry in pengajuan.transisi}  # oldest first: the latest one wins
    verifying = pengajuan.status == Status.MENUNGGU_SGI and pengajuan.diajukan_pada
    steps = []
    for index, (key, label) in enumerate(stages):
        is_current = index == reached
        steps.append({
            'label': label,
            'nomor': index + 1,
            'selesai': index < reached,
            'aktif': is_current,
            'dikembalikan': is_current and is_returned,
            'tugas': _tugas(pengajuan) if is_current else '',
            'catatan': latest[Aksi.DIKEMBALIKAN].catatan if is_current and is_returned and Aksi.DIKEMBALIKAN in latest else '',
            'oleh': latest.get(Aksi.DIAJUKAN) if key == 'pengajuan' and index < reached else None,
            'pics': _task_pics(pengajuan, pics) if key == 'pengajuan' and not verifying else [],
            'punya_pic': key == 'pengajuan',
            'durasi': format_durasi(spent[key]) if key in spent else '',
            'detail': _department_lines(pengajuan, pics, now) if key == 'sgi' and verifying else [],
        })
    return steps


def _is_imported(pengajuan):
    return pengajuan.status == Status.APPROVED and not pengajuan.disetujui_oleh_id


def _department_rows(requests, now):
    """How long each department of Verifikasi BC takes, to see who a request is waiting for."""
    sent = [p for p in requests if p.diajukan_pada]
    rows = []
    for department in Departemen:
        waiting = [_wait(p.diajukan_pada, now) for p in sent
                   if p.status == Status.MENUNGGU_SGI and department in _pending(p)]
        done = [_wait(p.diajukan_pada, row.waktu) for p in sent for row in p.verifikasi.all()
                if row.departemen == department.value and row.user_id]
        rows.append({
            'key': DEPARTMENT_ROW_KEY[department],
            'label': department.label,
            'sub': True,
            'open': len(waiting),
            'dikembalikan': None,
            'rata_tunggu': average_durasi(waiting),
            'terlama': longest_durasi(waiting),
            'rata_selesai': average_durasi(done),
        })
    return rows


def _belum_upload(scope):
    """Active Mitra Sites with no request yet this week: nothing to open, so counted apart."""
    started = PengajuanAreaKerja.objects.filter(minggu=this_monday()).values('mitra_site')
    return access.visible(scope).exclude(pk__in=started).count()


def context(scope, user, group_names, pics, show_all, requested_pk):
    requests = _load_requests(scope, user)
    now = timezone.now()
    times = {p.pk: _stage_times(p, now) for p in requests}
    mine = _mine(requests, scope, user, group_names, pics)
    tasks = [p for p in requests if p.terbuka] if show_all else mine
    rows = [_row(p, pics, times[p.pk]) for p in tasks]
    selected = pick_selected(rows, STAGES, requested_pk)
    timed = [(p, times[p.pk]) for p in requests if not _is_imported(p)]
    return {
        'rows': rows,
        'groups': build_groups(rows, STAGES, selected),
        'statistik': build_statistik(requests, times, mine, RETURNED, 0, _short_name),
        'sla_tahap': [*stage_summary(timed, WORK_STAGES, RETURNED), *_department_rows(requests, now)],
        'selected': selected,
        'selected_judul': f'{_judul(selected)} · {selected.minggu_label}' if selected else '',
        'steps': _progress(selected, pics, times[selected.pk], now) if selected else [],
        'belum_upload': _belum_upload(scope),
    }
