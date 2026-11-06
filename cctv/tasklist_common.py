"""What the Tasklist does the same for CCTV coverage and work areas.

A request here has `.tahap` (its stage) and `.terbuka` (not finished yet). A row is a dict with at
least 'p' (the request) and 'menunggu' (time in its current stage).
"""

from notifikasi.models import most_specific

from .sla import format_durasi


def covering(pics, tahap, place):
    """The PICs holding a stage for a CCTV or a Mitra Site (both have a site and a company)."""
    return most_specific(pic for pic in pics if pic.tahap == tahap and pic.covers(place))


def owned_by(tahap, role, place, user, group_names, pics):
    """The PICs of a stage hold its tasks; where none covers this place, the holders of its role do."""
    assigned = covering(pics, tahap, place)
    if assigned:
        return any(pic.karyawan and pic.karyawan.user_id == user.pk for pic in assigned)
    # The role itself, not scope.is_*: being PIC of another site must not bring this one along.
    return role in group_names


def build_groups(rows, stages, selected):
    """The tasks per stage, in flow order; only the stage of the selected task starts open."""
    groups = []
    for key, label in stages:
        stage_rows = [row for row in rows if row['p'].tahap == key]
        if stage_rows:
            groups.append({
                'key': key,
                'label': label,
                'rows': stage_rows,
                'buka': selected is not None and selected.tahap == key,
                'terlama': format_durasi(max(row['menunggu'] for row in stage_rows)),
            })
    return groups


def pick_selected(rows, stages, requested_pk):
    """The task asked for, else the first one shown. Anything outside the list cannot be selected."""
    selected = next((row['p'] for row in rows if str(row['p'].pk) == requested_pk), None)
    if selected is None and rows:
        selected = build_groups(rows, stages, None)[0]['rows'][0]['p']
    return selected


def build_statistik(requests, times, mine, returned, warnings, nama):
    """The cards above the list. `nama` gives a request's short name for the longest wait."""
    open_requests = [p for p in requests if p.terbuka]
    longest = max(open_requests, key=lambda p: times[p.pk][p.tahap], default=None)
    return {
        'total': len(requests),
        'open': len(open_requests),
        'tugas_saya': len(mine),
        'dikembalikan': sum(p.status in returned for p in open_requests),
        'selesai': len(requests) - len(open_requests),
        'selesai_warning': warnings,
        'selesai_label': f'Approved ({warnings} with warning)' if warnings else 'Approved',
        'terlama': format_durasi(times[longest.pk][longest.tahap]) if longest else '',
        'terlama_nama': nama(longest) if longest else '',
    }
