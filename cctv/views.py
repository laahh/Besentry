import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import BooleanField, Count, ExpressionWrapper, F, FloatField, Func, Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import DetailLokasi, Perusahaan, Site

from . import history
from .access import get_scope
from .forms import CctvAksesForm, CctvForm, CctvSpecForm
from .models import Cctv, CctvAkses

PER_PAGE = 50
RIWAYAT_LIMIT = 30
SEARCH_MAX = 100
CHOICE_FILTERS = {
    'kategori': Cctv.Kategori,
    'jenis': Cctv.Jenis,
    'status': Cctv.Status,
    'kondisi': Cctv.Kondisi,
}
LIST_FIELDS = (
    'no_cctv', 'nama', 'jenis', 'fungsi', 'status', 'kondisi', 'lokasi_pemasangan',
    'site__kode', 'perusahaan__nama', 'control_room__nama',
)


def _scope(user):
    scope = get_scope(user)
    if not scope.can_open:
        raise PermissionDenied
    return scope


def _filtered(scope, params):
    """Visible CCTV matching the list filters in `params`, plus the filters as the template needs them."""
    selected = {}
    lookups = {}
    for name in ('site', 'perusahaan'):
        value = params.get(name, '')
        if value.isdigit():
            selected[name] = value
            lookups[f'{name}_id'] = int(value)
    for name, choices in CHOICE_FILTERS.items():
        value = params.get(name, '')
        if value in choices.values:
            selected[name] = value
            lookups[name] = value

    queryset = scope.filter(Cctv.objects.filter(**lookups))
    q = params.get('q', '').strip()[:SEARCH_MAX]
    if q:
        selected['q'] = q
        queryset = queryset.filter(
            Q(no_cctv__icontains=q) | Q(nama__icontains=q) | Q(lokasi_pemasangan__icontains=q)
        )
    return queryset, selected


@login_required
def index(request):
    scope = _scope(request.user)
    queryset, selected = _filtered(scope, request.GET)

    not_baik = ~Q(kondisi=Cctv.Kondisi.BAIK)
    stats = queryset.aggregate(
        total=Count('pk'),
        live=Count('pk', filter=Q(status=Cctv.Status.LIVE)),
        bermasalah=Count('pk', filter=not_baik),
        tanpa_posisi=Count('pk', filter=Q(posisi__isnull=True)),
    )
    rows = queryset.select_related('site', 'perusahaan', 'control_room').only('pk', *LIST_FIELDS)
    paginator = Paginator(rows, PER_PAGE)
    paginator.count = stats['total']  # already counted above; saves a COUNT query
    page = paginator.get_page(request.GET.get('page'))

    # Filter options: only sites/perusahaan with CCTV the user can see (one subquery each).
    # Perusahaan also holds every SID company, most of which have no CCTV.
    visible = scope.filter(Cctv.objects.all())
    sites = Site.objects.only('pk', 'kode').filter(pk__in=visible.values('site_id'))
    perusahaan = Perusahaan.objects.only('pk', 'nama').filter(pk__in=visible.values('perusahaan_id'))

    return render(request, 'cctv/index.html', {
        'page_obj': page,
        'stats': stats,
        'selected': selected,
        'sites': sites,
        'perusahaan_list': perusahaan,
        'choices': {name: choices.choices for name, choices in CHOICE_FILTERS.items()},
        'scope': scope,
    })


@login_required
def geojson(request):
    """CCTV points for the map, with the same filters as the list. Coordinates come from
    PostGIS directly (ST_X/ST_Y), so no geometry objects are built in Python."""
    queryset, _ = _filtered(_scope(request.user), request.GET)
    rows = (
        queryset.exclude(posisi__isnull=True)
        .annotate(
            lon=Func(F('posisi'), function='ST_X', output_field=FloatField()),
            lat=Func(F('posisi'), function='ST_Y', output_field=FloatField()),
        )
        .values_list('pk', 'no_cctv', 'nama', 'kondisi', 'lon', 'lat')
    )
    features = [
        {
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [round(lon, 6), round(lat, 6)]},
            'properties': {'id': pk, 'no': no, 'nama': nama, 'kondisi': kondisi},
        }
        for pk, no, nama, kondisi, lon, lat in rows.iterator(chunk_size=2000)
    ]
    return JsonResponse({'type': 'FeatureCollection', 'features': features})


class _Echo:
    def write(self, value):
        return value


EXPORT_COLUMNS = (
    ('No. CCTV', 'no_cctv'), ('Nama CCTV', 'nama'), ('Site', 'site__kode'), ('Perusahaan', 'perusahaan__nama'),
    ('Kategori', 'kategori'), ('Fungsi', 'fungsi'), ('Bentuk Instalasi', 'bentuk_instalasi'), ('Jenis', 'jenis'),
    ('Lokasi Pemasangan', 'lokasi_pemasangan'), ('Control Room', 'control_room__nama'),
    ('id lokasi', 'lokasi_id'), ('Lokasi', 'lokasi__nama'),
    ('id detail lokasi', 'detail_lokasi_id'), ('Detail Lokasi', 'detail_lokasi__nama'),
    ('Kategori Area', 'kategori_area'), ('Kategori Aktivitas', 'kategori_aktivitas'),
    ('Status', 'status'), ('Kondisi', 'kondisi'), ('Longitude', 'lon'), ('Latitude', 'lat'),
    ('Connected', 'connected'), ('Mirrored', 'mirrored'), ('Auto Alert', 'auto_alert'),
    ('Brand', 'spec__brand'), ('Model', 'spec__model'), ('Radius (m)', 'spec__radius_m'),
    ('Periode Update', 'periode_update'), ('Keterangan', 'keterangan'),
)
CHOICE_COLUMNS = {
    'kategori', 'fungsi', 'bentuk_instalasi', 'jenis', 'kategori_area', 'kategori_aktivitas', 'status', 'kondisi',
}


@login_required
def export_csv(request):
    """Filtered list as CSV, streamed row by row (no access data)."""
    queryset, _ = _filtered(_scope(request.user), request.GET)
    rows = queryset.annotate(
        lon=Func(F('posisi'), function='ST_X', output_field=FloatField()),
        lat=Func(F('posisi'), function='ST_Y', output_field=FloatField()),
    ).values_list(*(column for _, column in EXPORT_COLUMNS))

    writer = csv.writer(_Echo())
    # Same labels as the spreadsheet ("FIXED", "Live View"), not the stored codes.
    labels = {
        index: dict(Cctv._meta.get_field(column).choices)
        for index, (_, column) in enumerate(EXPORT_COLUMNS)
        if column in CHOICE_COLUMNS
    }

    def cell(index, value):
        if value is None:
            return ''
        if isinstance(value, bool):
            return 'Yes' if value else 'No'
        return labels[index].get(value, value) if index in labels else value

    def lines():
        yield '﻿'  # BOM, so Excel opens it as UTF-8
        yield writer.writerow([header for header, _ in EXPORT_COLUMNS])
        for row in rows.iterator(chunk_size=2000):
            yield writer.writerow([cell(index, value) for index, value in enumerate(row)])

    response = StreamingHttpResponse(lines(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="cctv-{timezone.localdate():%Y%m%d}.csv"'
    return response


def _akses(cctv_pk):
    """Access row without decrypting the password."""
    return (
        CctvAkses.objects.defer('password')
        .annotate(ada_password=ExpressionWrapper(~Q(password=''), output_field=BooleanField()))
        .filter(pk=cctv_pk)
        .first()
    )


@login_required
def detail(request, pk):
    scope = _scope(request.user)
    # Out-of-scope CCTV answer 404, so their existence is not revealed.
    cctv = get_object_or_404(
        scope.filter(Cctv.objects.select_related(
            'site', 'perusahaan', 'control_room', 'lokasi', 'detail_lokasi', 'spec',
        )),
        pk=pk,
    )
    return render(request, 'cctv/detail.html', {
        'cctv': cctv,
        'spec': getattr(cctv, 'spec', None),
        'akses': _akses(pk) if scope.can_see_akses else None,
        'can_akses': scope.can_see_akses,
        'can_edit': scope.can_edit(cctv),
        'can_delete': scope.can_delete,
        'riwayat': history.riwayat(pk, RIWAYAT_LIMIT),
    })


def _form(request, scope, cctv=None):
    can_akses = scope.can_see_akses
    data = request.POST or None
    spec = getattr(cctv, 'spec', None) if cctv else None
    akses = _akses(cctv.pk) if cctv and can_akses else None

    form = CctvForm(data, instance=cctv, scope=scope)
    spec_form = CctvSpecForm(data, instance=spec, prefix='spec')
    akses_form = CctvAksesForm(data, instance=akses, prefix='akses') if can_akses else None
    forms = [f for f in (form, spec_form, akses_form) if f]

    if request.method == 'POST' and all(f.is_valid() for f in forms):
        with transaction.atomic(), transaction.atomic(using='log'):
            saved = form.save()
            # Spec/akses rows are only created once something is filled in.
            for related_form, existing in ((spec_form, spec), (akses_form, akses)):
                if related_form and (existing or related_form.has_changed()):
                    related_form.instance.cctv = saved
                    related_form.save()
        messages.success(request, f'CCTV {saved.no_cctv} disimpan.')
        return redirect('cctv:detail', pk=saved.pk)

    return render(request, 'cctv/form.html', {
        'cctv': cctv,
        'form': form,
        'spec_form': spec_form,
        'akses_form': akses_form,
        'ada_password': bool(akses and akses.ada_password),
    })


@login_required
def create(request):
    scope = _scope(request.user)
    if not scope.can_create:
        raise PermissionDenied
    return _form(request, scope)


@login_required
def update(request, pk):
    scope = _scope(request.user)
    cctv = get_object_or_404(scope.filter(Cctv.objects.select_related('spec')), pk=pk)
    if not scope.can_edit(cctv):
        raise PermissionDenied
    return _form(request, scope, cctv)


@login_required
@require_POST
def delete(request, pk):
    scope = _scope(request.user)
    if not scope.can_delete:
        raise PermissionDenied
    cctv = get_object_or_404(Cctv, pk=pk)
    try:
        with transaction.atomic(), transaction.atomic(using='log'):
            cctv.delete()
    except ProtectedError:
        messages.error(request, f'CCTV {cctv.no_cctv} masih punya data coverage, jadi tidak bisa dihapus.')
        return redirect('cctv:detail', pk=pk)
    messages.success(request, f'CCTV {cctv.no_cctv} dihapus.')
    return redirect('cctv:index')


@login_required
@require_POST
def password(request, pk):
    """Decrypts one password on request, so it is never part of a rendered page."""
    scope = _scope(request.user)
    if not scope.can_see_akses:
        raise PermissionDenied
    visible = scope.filter(Cctv.objects.all()).values('pk')
    akses = get_object_or_404(CctvAkses.objects.only('pk', 'password').filter(cctv__in=visible), pk=pk)
    response = JsonResponse({'password': akses.password})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def detail_lokasi_options(request):
    """Options for the detail lokasi select once a lokasi is picked."""
    _scope(request.user)
    lokasi = request.GET.get('lokasi', '')
    if not lokasi.isdigit():
        return JsonResponse({'options': []})
    options = list(DetailLokasi.objects.filter(lokasi_id=int(lokasi)).values('id', 'nama'))
    return JsonResponse({'options': options})
