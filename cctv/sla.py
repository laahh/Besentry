"""How long requests have spent in each stage, read from their history. Used for CCTV coverage here
and for work areas in areakerja.tasklist.

Each history event closes one stage: the time since the event before it counts for that stage, so a
request that comes back to a stage adds to the time it already has there. Clock time, 24 hours a day.
"""

from datetime import timedelta

from .models import PengajuanCoverage, RiwayatPengajuan

Aksi = RiwayatPengajuan.Aksi
Status = PengajuanCoverage.Status

CLOSES = {
    Aksi.DIAJUKAN: 'it',
    Aksi.OTOMATIS: 'it',
    Aksi.DIGITASI: 'mitra',
    Aksi.DISETUJUI_SGI: 'sgi',
    Aksi.DISETUJUI_SAFETY: 'safety',
    Aksi.DISETUJUI_WARNING: 'safety',
}
RETURNED = (Status.RETURN_IT, Status.RETURN_MITRA, Status.RETURN_SGI)
# Only these can return a request; cctv.coverage starts the note of the return with their name.
RETURNED_BY = {'Survey BC': 'sgi', 'Safety BC': 'safety'}
# The history entries stage_times reads, to load no others.
TRANSITIONS = (*CLOSES, Aksi.DIKEMBALIKAN)


def accumulate(start, events, open_stage, now):
    """{stage: time spent there} from [(stage closed, when)], with open_stage counted up to now."""
    spent, since = {}, start
    for stage, moment in events:
        spent[stage] = spent.get(stage, timedelta()) + max(moment - since, timedelta())
        since = moment
    if open_stage:
        spent[open_stage] = spent.get(open_stage, timedelta()) + max(now - since, timedelta())
    return spent


def _closed_stage(entry, is_combined):
    if entry.aksi == Aksi.DIKEMBALIKAN:
        return next((stage for name, stage in RETURNED_BY.items() if entry.catatan.startswith(name)), None)
    stage = CLOSES.get(entry.aksi)
    return 'sgi' if stage == 'mitra' and is_combined else stage


def stage_times(pengajuan, entries, now):
    """{stage: time spent there} for one CCTV coverage request."""
    closed = ((_closed_stage(entry, pengajuan.gabung), entry.waktu) for entry in entries)
    events = [(stage, moment) for stage, moment in closed if stage]
    return accumulate(pengajuan.dibuat, events, pengajuan.tahap if pengajuan.terbuka else None, now)


def format_durasi(value):
    """The two largest units: '3 hari 11 jam', '2 jam 5 menit', '12 menit'."""
    minutes = int(value.total_seconds() // 60)
    if minutes < 1:
        return '< 1 menit'
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = [(days, 'hari'), (hours, 'jam')] if days else [(hours, 'jam'), (minutes, 'menit')]
    return ' '.join(f'{amount} {unit}' for amount, unit in parts if amount)


def average_durasi(values):
    return format_durasi(sum(values, timedelta()) / len(values)) if values else ''


def longest_durasi(values):
    return format_durasi(max(values)) if values else ''


def stage_summary(items, stages, returned=RETURNED):
    """One row per (key, label) of stages, from [(request, its stage times)]."""
    rows = []
    for key, label in stages:
        waiting = [(p, spent.get(key, timedelta())) for p, spent in items if p.tahap == key]
        passed = [spent[key] for p, spent in items if p.tahap != key and key in spent]
        times = [time for _, time in waiting]
        rows.append({
            'key': key,
            'label': label,
            'open': len(waiting),
            'dikembalikan': sum(p.status in returned for p, _ in waiting),
            'rata_tunggu': average_durasi(times),
            'terlama': longest_durasi(times),
            'rata_selesai': average_durasi(passed),
        })
    return rows
