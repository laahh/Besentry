"""Dashboard Coverage: CCTV coverage of the work areas per week, for Survey BC, Mining BC, Safety BC and admins.

The page is rendered with the numbers (filters reload it, as on Area Kerja); the map fetches its layers
from the GeoJSON views below with the same query string. In fullscreen the map's own panel changes the
filter without a reload (a reload would leave fullscreen): it asks `data_json` for the KPI and layer URLs.
"""

import datetime
import io
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.formats import number_format
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from areakerja.models import JenisArea, ModeKalkulasi
from cctv.coverage import this_monday
from cctv.models import minggu_label

from . import coverage
from .coverage import LAPISAN, Filter

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _filter(request):
    if not coverage.can_open(request.user):
        raise PermissionDenied
    return Filter.from_query(request.GET)


def _geojson(data, f):
    response = JsonResponse(data)
    # A locked week never changes; the running week is recomputed after every change.
    response['Cache-Control'] = 'private, max-age=60' if f.live else 'private, max-age=3600'
    return response


def _peta(f):
    query = urlencode(f.query())
    return {
        'lapisan': {name: f"{reverse('coverage:lapisan', args=[name])}?{query}" for name in LAPISAN},
        'area': f"{reverse('coverage:area')}?{query}",
        'cctv': f"{reverse('coverage:cctv')}?{query}",
        'cctv_detail': reverse('cctv:detail', args=[0]),
        'ortho': reverse('orthophoto:current'),
        'data': reverse('coverage:data'),
        # All sites: open on the whole orthophoto; one site: on its work areas.
        'fit': 'area' if f.site else 'ortho',
        'site': f.site,
    }


def _kpi(f, rows):
    kpi = coverage.kpi(rows, f.mode)
    teks = {key: (f'{number_format(value, 1)}%' if value is not None else '-') for key, value in kpi['persen'].items()}
    teks['luas'] = number_format(kpi['luas'], 2)
    return kpi, teks


def _minggu(f):
    """The week as the page shows it, with the weeks before and after (none after this week)."""
    akhir = f.minggu + datetime.timedelta(days=6)
    return {
        'minggu': f.minggu, 'akhir': akhir, 'label': minggu_label(f.minggu), 'live': f.live,
        'sebelum': f.minggu - datetime.timedelta(days=7),
        'sesudah': f.minggu + datetime.timedelta(days=7) if f.minggu < this_monday() else None,
    }


@login_required
def data_json(request):
    """KPI and layer URLs for the map's fullscreen panel."""
    f = _filter(request)
    kpi, teks = _kpi(f, list(coverage.baris(f)))
    week = _minggu(f)
    return JsonResponse({
        'kpi': kpi, 'kpi_teks': teks, 'query': urlencode(f.query()), 'peta': _peta(f),
        'garis_waktu': coverage.garis_waktu(f),
        'minggu': week['minggu'].isoformat(), 'label': week['label'], 'live': week['live'],
        'rentang': f"{week['minggu']:%d %b} – {week['akhir']:%d %b %Y}",
        'sebelum': week['sebelum'].isoformat(), 'sesudah': week['sesudah'].isoformat() if week['sesudah'] else None,
    })


@login_required
def page(request):
    f = _filter(request)
    rows = list(coverage.baris(f))
    sites, perusahaan = coverage.pilihan()
    query = urlencode(f.query())
    filter_qs = urlencode(f.query(minggu=''))  # kept when moving to another week
    kpi, kpi_teks = _kpi(f, rows)
    tabel = coverage.tabel(rows, f.mode)
    return render(request, 'dashboard/coverage.html', {
        'f': f, 'kpi': kpi, 'kpi_teks': kpi_teks, 'tabel': [g for g in tabel if g['total']['luas']],
        'tanpa_area': [g for g in tabel if not g['total']['luas']],
        'menunggu': coverage.menunggu(f), 'tren': coverage.tren(f), 'dihitung_pada': coverage.dihitung_pada(f),
        'minggu': f.minggu, 'minggu_ini': this_monday(), 'akhir': f.minggu + datetime.timedelta(days=6),
        'sebelum': f.minggu - datetime.timedelta(days=7),
        'sesudah': f.minggu + datetime.timedelta(days=7) if f.minggu < this_monday() else None,
        'sites': sites, 'perusahaan_list': perusahaan, 'jenis_list': JenisArea.choices, 'mode_list': ModeKalkulasi.choices,
        'filter_qs': filter_qs, 'query': query,
        'week_picker': {'dilihat': f.minggu.isoformat(), 'berjalan': this_monday().isoformat(), 'filter': filter_qs},
        'peta': _peta(f), 'garis_waktu': coverage.garis_waktu(f),
    })


@login_required
def lapisan_geojson(request, lapisan):
    f = _filter(request)
    if lapisan not in LAPISAN:
        raise Http404
    return _geojson(coverage.geojson(f, lapisan), f)


@login_required
def area_geojson(request):
    f = _filter(request)
    return _geojson(coverage.area_kerja(f), f)


@login_required
def cctv_geojson(request):
    f = _filter(request)
    return _geojson(coverage.cctv_titik(f), f)


@login_required
def export_xlsx(request):
    """The page's table as a workbook: one line per Mitra Site and kind, with its total."""
    f = _filter(request)
    rows = list(coverage.baris(f))
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Coverage'
    mode_label = dict(ModeKalkulasi.choices)[f.mode]
    sheet.append([f'Coverage CCTV terhadap area kerja · {minggu_label(f.minggu)} · {mode_label}'])
    sheet['A1'].font = Font(bold=True, size=12)
    sheet.append([])
    header = ['Site', 'Perusahaan', 'Jenis area', 'Luas (ha)', 'Tercover (ha)', 'Potensi (ha)', 'Tidak tercover (ha)',
              '% Tercover', '% Potensi', '% Tidak tercover', 'Jumlah CCTV', 'Area week', 'Catatan']
    sheet.append(header)
    for cell in sheet[3]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='134E2B')
    total_font = Font(bold=True)
    for group in coverage.tabel(rows, f.mode):
        for line in group['jenis']:
            sheet.append([group['site'], group['perusahaan'], line['label'], line['luas'], line['tercover'], line['potensi'],
                          line['tidak'], line['persen']['tercover'], line['persen']['potensi'], line['persen']['tidak'],
                          line['jumlah_cctv'], group['minggu_area_label'], group['catatan']])
        total = group['total']
        sheet.append([group['site'], group['perusahaan'], 'Total', total['luas'], total['tercover'], total['potensi'],
                      total['tidak'], total['persen']['tercover'], total['persen']['potensi'], total['persen']['tidak'],
                      None, group['minggu_area_label'], group['catatan']])
        for cell in sheet[sheet.max_row]:
            cell.font = total_font
    summary = coverage.kpi(rows, f.mode)
    sheet.append([])
    sheet.append(['Total semua', None, None, summary['luas'], summary['tercover'], summary['potensi'], summary['tidak'],
                  summary['persen']['tercover'], summary['persen']['potensi'], summary['persen']['tidak']])
    for cell in sheet[sheet.max_row]:
        cell.font = total_font
    for column, width in zip('ABCDEFGHIJKLM', (10, 34, 18, 11, 13, 12, 18, 11, 11, 15, 12, 11, 40)):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX)
    name = minggu_label(f.minggu).replace(' ', '-')
    response['Content-Disposition'] = f'attachment; filename="coverage-{name}-{f.mode}.xlsx"'
    return response
