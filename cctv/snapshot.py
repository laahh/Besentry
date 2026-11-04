"""Weekly snapshot: every CCTV, its boundary and its request status when a week closes.

A week closes on Sunday 24:00 WITA (Monday 00:00). Once a week has rows it is locked:
close_week() never touches it again.
"""

import datetime

from django.db import transaction
from django.db.models import OuterRef, Subquery

from .coverage import this_monday
from .models import Cctv, CctvCoverage, CctvSpec, PengajuanCoverage, SnapshotMingguan

SKIP_FIELDS = {'id', 'posisi', 'dibuat', 'diubah'}
SPEC_FIELDS = [f.name for f in CctvSpec._meta.concrete_fields if f.name != 'cctv']


def last_closed_week():
    return this_monday() - datetime.timedelta(days=7)


def _plain(value):
    return value.isoformat() if isinstance(value, (datetime.date, datetime.datetime)) else value


def boundaries():
    """{cctv_id: boundary as the map shows it}: an open request's once it has been through digitisation
    (see coverage.map_coverage), else the approved one."""
    approved = dict(
        CctvCoverage.objects.order_by('cctv_id', '-minggu').distinct('cctv_id').values_list('cctv_id', 'geom')
    )
    drafts = dict(
        PengajuanCoverage.objects.exclude(status__in=PengajuanCoverage.SELESAI)
        .filter(cctv__isnull=False, geom__isnull=False, digitasi_pada__isnull=False).values_list('cctv_id', 'geom')
    )
    return {**approved, **drafts}


@transaction.atomic
def close_week(minggu=None):
    """Write the snapshot of the week starting `minggu` (default: the week that just ended).
    Returns the number of rows written; 0 when the week was already closed."""
    minggu = minggu or last_closed_week()
    if SnapshotMingguan.objects.filter(minggu=minggu).exists():
        return 0
    latest = PengajuanCoverage.objects.filter(cctv=OuterRef('pk')).order_by('-dibuat', '-pk')
    cctvs = Cctv.objects.select_related('spec').annotate(
        pengajuan_status=Subquery(latest.values('status')[:1]),
        pengajuan_minggu=Subquery(latest.values('minggu')[:1]),
    )
    fields = [f for f in Cctv._meta.concrete_fields if f.name not in SKIP_FIELDS]
    boundary_of = boundaries()
    rows = []
    for cctv in cctvs.iterator(chunk_size=500):
        spec = getattr(cctv, 'spec', None)
        data = {f.attname: _plain(getattr(cctv, f.attname)) for f in fields}
        data['spec'] = {name: getattr(spec, name) for name in SPEC_FIELDS} if spec else None
        rows.append(SnapshotMingguan(
            minggu=minggu, cctv_id=cctv.pk, no_cctv=cctv.no_cctv, data=data, posisi=cctv.posisi,
            coverage=boundary_of.get(cctv.pk), status_pengajuan=cctv.pengajuan_status or '',
            minggu_pengajuan=cctv.pengajuan_minggu,
        ))
    SnapshotMingguan.objects.bulk_create(rows, batch_size=500)
    return len(rows)
