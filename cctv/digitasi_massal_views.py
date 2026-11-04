"""Upload one shapefile for many digitisation tasks, instead of opening them one by one.

The same rules as a single upload apply to each feature; its `no_cctv` column says which CCTV it is
for. 'Cek' reports without saving. 'Kirim' reads the file again and sends what passes to Survey BC;
the rest stays with Survey Mitra. Nothing is kept between the two steps.
"""

from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.gis.geos import MultiPolygon
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from notifikasi import services as notifikasi
from orthophoto.models import Orthophoto

from . import coverage
from .models import PengajuanCoverage
from .pengajuan_forms import DigitasiMassalForm
from .pengajuan_views import _can_mitra, _report_sent, _scope, _visible

Status = PengajuanCoverage.Status
RESULT_TEMPLATE = 'cctv/partials/digitasi_massal_hasil.html'


def _tasks(scope, user):
    """The requests this user may digitise now, by CCTV number."""
    requests = _visible(scope, user).filter(status__in=(Status.MENUNGGU_MITRA, Status.RETURN_MITRA))
    return {p.cctv.no_cctv.strip().upper(): p for p in requests if _can_mitra(scope, p)}


def _check_feature(feature, tasks, counts):
    no_cctv = feature['no_cctv']
    key = no_cctv.upper()
    row = {'no_cctv': no_cctv or '(kosong)', 'pengajuan': tasks.get(key), 'geom': None, 'valid': False, 'pesan': ''}
    if not no_cctv:
        row['pesan'] = f'Kolom {coverage.SHP_CCTV_COLUMN} kosong.'
    elif counts[key] > 1:
        row['pesan'] = f'CCTV ini muncul {counts[key]} kali di file.'
    elif row['pengajuan'] is None:
        row['pesan'] = 'Tidak ada tugas digitasi untuk CCTV ini.'
    elif feature['error']:
        row['pesan'] = feature['error']
    else:
        try:
            polygon, note = coverage.editable_polygon(feature['geom'])
            geom = MultiPolygon(polygon, srid=4326)
            summary = coverage.validate_geometry(geom, row['pengajuan'].cctv)
        except coverage.GeometryError as exc:
            row['pesan'] = str(exc)
        else:
            row.update(geom=geom, valid=True, pesan=f'{summary}. {note}'.strip())
    return row


def _check(upload, tasks):
    """(rows with the problems first, numbers of the tasks the file does not cover)."""
    features = coverage.read_features_zip(upload)
    counts = Counter(feature['no_cctv'].upper() for feature in features)
    rows = [_check_feature(feature, tasks, counts) for feature in features]
    rows.sort(key=lambda row: row['valid'])
    uncovered = sorted(task.cctv.no_cctv for key, task in tasks.items() if key not in counts)
    return rows, uncovered


def _send(rows, user, nama_file):
    orthophoto = Orthophoto.objects.filter(aktif=True).first()
    sent, failed = [], 0
    for row in rows:
        if not row['valid']:
            failed += 1
            continue
        try:
            coverage.save_digitasi(row['pengajuan'], user, row['geom'], kirim=True, sumber='shp',
                                   file_shp=nama_file, catatan='upload SHP sekaligus', orthophoto=orthophoto)
            sent.append(row['pengajuan'])
        except (coverage.GeometryError, coverage.ReviewError):  # moved on since it was read
            failed += 1
    return sent, failed


def _form_error(form):
    return '; '.join(error for errors in form.errors.values() for error in errors)


@login_required
def digitasi_massal(request):
    scope = _scope(request.user)
    if not (scope.full or scope.is_mitra):
        raise PermissionDenied
    tasks = _tasks(scope, request.user)
    if request.method != 'POST':
        return render(request, 'cctv/digitasi_massal.html', {
            'form': DigitasiMassalForm(),
            'jumlah_tugas': len(tasks),
            'kolom': coverage.SHP_CCTV_COLUMN,
        })

    form = DigitasiMassalForm(request.POST, request.FILES)
    wants_check = request.POST.get('aksi') == 'cek'
    error, rows, uncovered = '', [], []
    if form.is_valid():
        try:
            rows, uncovered = _check(form.cleaned_data['file_shp'], tasks)
        except coverage.GeometryError as exc:
            error = str(exc)
    else:
        error = _form_error(form)

    if wants_check:
        return render(request, RESULT_TEMPLATE, {
            'error': error, 'rows': rows, 'uncovered': uncovered,
            'jumlah_lolos': sum(row['valid'] for row in rows),
            'jumlah_gagal': sum(not row['valid'] for row in rows),
        }, status=400 if error else 200)

    if error:
        messages.error(request, error)
        return redirect('cctv:digitasi_massal')
    sent, failed = _send(rows, request.user, form.cleaned_data['file_shp'].name)
    if sent:
        messages.success(request, f'{len(sent)} CCTV dikirim ke Survey BC.')
        _report_sent(request, notifikasi.notify_batch(sent, request.user), sent[0].get_status_display())
    if failed:
        messages.warning(request, f'{failed} feature tidak terkirim. Perbaiki lalu upload ulang bagian itu.')
    return redirect('cctv:tasklist')
