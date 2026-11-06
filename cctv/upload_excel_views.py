"""Add many CCTV from one Excel file, instead of the "Tambah CCTV" form one by one.

Every row goes through the same forms and the same request flow as that form. Continuing to
digitisation needs a screenshot there, so a row with one in the zip continues, and a row without
becomes an IT draft to finish later. 'Cek' reports without saving; 'Proses' reads the files again and
saves what passes. Nothing is kept between the two steps.
"""

from collections import Counter
from contextlib import nullcontext

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render

from notifikasi import services as notifikasi

from . import coverage
from .excel_cctv import ExcelError, MAX_ROWS, ScreenshotError, Screenshots, build_template, read_rows
from .models import Cctv, PengajuanCoverage
from .pengajuan_forms import UploadExcelForm, draft_data
from .pengajuan_views import _attach, _it_forms, _report_sent, _save_new_cctv, _scope

Status = PengajuanCoverage.Status
RESULT_TEMPLATE = 'cctv/partials/upload_excel_hasil.html'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
TO_DIGITIZE = 'Lanjut digitasi'
TO_DRAFT = 'Draft IT'


def _creator_scope(user):
    scope = _scope(user)
    if not scope.can_create:
        raise PermissionDenied
    return scope


def _taken_numbers():
    """Numbers already used by a CCTV or by a draft of a new CCTV, in capitals."""
    numbers = {number.strip().upper() for number in Cctv.objects.values_list('no_cctv', flat=True)}
    drafts = PengajuanCoverage.objects.filter(cctv__isnull=True).values_list('data_draft', flat=True)
    in_drafts = {(draft.get('no_cctv') or [''])[0].strip().upper() for draft in drafts}
    return numbers, in_drafts - {''}


def _number_problems(no_cctv, counts, numbers, in_drafts):
    key = no_cctv.strip().upper()
    if not key:
        return []  # the form says it is required
    if counts[key] > 1:
        return [f'No. CCTV muncul {counts[key]} kali di file.']
    if key in numbers:
        return ['No. CCTV sudah ada di CCTV Database.']
    if key in in_drafts:
        return ['No. CCTV sudah ada di draft pengajuan.']
    return []


def _form_problems(*forms):
    problems = []
    for form in forms:
        if form is None or form.is_valid():
            continue
        for name, errors in form.errors.items():
            label = '' if name == '__all__' else f'{form[name].label}: '
            problems += [f'{label}{error}' for error in errors]
    return problems


def _save_row(forms, it_form, data, user):
    """Saves one row; returns the request when it continued to digitisation."""
    if it_form.cleaned_data['aksi'] == 'lanjutkan':
        _, pengajuan = _save_new_cctv(forms, None, it_form, user, None)
        return pengajuan
    pengajuan = PengajuanCoverage(jenis=PengajuanCoverage.Jenis.BARU, diajukan_oleh=user)
    _attach(pengajuan, it_form)
    coverage.save_draft(pengajuan, user, draft_data(data))
    return None


def _handle_row(row, scope, user, shots, taken, counts, save):
    result = {'baris': row.baris, 'no_cctv': row.no_cctv or '(kosong)', 'nama': row.nama, 'valid': False,
              'tujuan': '', 'pesan': '', 'pengajuan': None}
    problems = [*row.errors, *_number_problems(row.no_cctv, counts, *taken)]
    shot = None
    try:
        shot = shots.get(row.no_cctv)
    except ScreenshotError as exc:
        problems.append(str(exc))
    if problems:
        result['pesan'] = ' '.join(problems)
        return result
    data = row.data.copy()
    data['aksi'] = 'lanjutkan' if shot else 'draft'
    forms = _it_forms(scope, None, data, {'tangkapan_layar': shot} if shot else None)
    problems = _form_problems(forms['form'], forms['spec_form'], forms['akses_form'], forms['it_form'])
    if problems:
        result['pesan'] = ' '.join(problems)
        return result
    result.update(valid=True, tujuan=TO_DIGITIZE if shot else TO_DRAFT)
    if not save:
        return result
    try:
        with transaction.atomic(), transaction.atomic(using='log'):  # savepoints: one row fails alone
            result['pengajuan'] = _save_row(forms, forms['it_form'], data, user)
    except (IntegrityError, coverage.GeometryError, coverage.ReviewError) as exc:
        result.update(valid=False, tujuan='', pesan=f'Gagal disimpan: {exc}')
    return result


def _handle_all(form, scope, user, save):
    """(results with the problems first, screenshots no row used). Raises ExcelError."""
    rows = read_rows(form.cleaned_data['file_excel'], scope)
    shots = Screenshots(form.cleaned_data.get('file_gambar'))
    taken = _taken_numbers()
    counts = Counter(row.no_cctv.strip().upper() for row in rows)
    # One transaction for the whole file: live coverage is recomputed once, not once per row.
    outer = (transaction.atomic(), transaction.atomic(using='log')) if save else (nullcontext(), nullcontext())
    try:
        with outer[0], outer[1]:
            results = [_handle_row(row, scope, user, shots, taken, counts, save) for row in rows]
        unused = shots.unused
    finally:
        shots.close()
    results.sort(key=lambda result: result['valid'])
    return results, unused


def _form_error(form):
    return '; '.join(error for errors in form.errors.values() for error in errors)


def _notify(request, requests, user):
    """One summary per person for each stage reached (PT Berau Coal goes straight to Survey BC)."""
    groups = {}
    for pengajuan in requests:
        groups.setdefault((pengajuan.status, pengajuan.gabung), []).append(pengajuan)
    for group in groups.values():
        _report_sent(request, notifikasi.notify_batch(group, user), group[0].get_status_display())


def _report_saved(request, results, user):
    sent = [result['pengajuan'] for result in results if result['pengajuan']]
    drafts = sum(result['tujuan'] == TO_DRAFT for result in results)
    failed = sum(not result['valid'] for result in results)
    if sent:
        messages.success(request, f'{len(sent)} CCTV ditambahkan dan diteruskan ke digitasi.')
        _notify(request, sent, user)
    if drafts:
        messages.info(request, f'{drafts} CCTV disimpan sebagai Draft IT. Lengkapi tangkapan layarnya lewat Tasklist.')
    if failed:
        messages.warning(request, f'{failed} baris tidak tersimpan. Perbaiki lalu upload ulang baris itu.')


@login_required
def template_excel(request):
    scope = _creator_scope(request.user)
    response = HttpResponse(build_template(scope), content_type=XLSX)
    response['Content-Disposition'] = 'attachment; filename="template_upload_cctv.xlsx"'
    return response


@login_required
def upload_excel(request):
    scope = _creator_scope(request.user)
    if request.method != 'POST':
        return render(request, 'cctv/upload_excel.html', {'form': UploadExcelForm(), 'max_baris': MAX_ROWS})

    form = UploadExcelForm(request.POST, request.FILES)
    wants_check = request.POST.get('aksi') == 'cek'
    error, results, unused = '', [], []
    if form.is_valid():
        try:
            results, unused = _handle_all(form, scope, request.user, save=not wants_check)
        except ExcelError as exc:
            error = str(exc)
    else:
        error = _form_error(form)

    if wants_check:
        return render(request, RESULT_TEMPLATE, {
            'error': error, 'rows': results, 'gambar_lebih': unused,
            'jumlah_lolos': sum(result['valid'] for result in results),
            'jumlah_gagal': sum(not result['valid'] for result in results),
            'jumlah_digitasi': sum(result['tujuan'] == TO_DIGITIZE for result in results),
            'jumlah_draft': sum(result['tujuan'] == TO_DRAFT for result in results),
        }, status=400 if error else 200)

    if error:
        messages.error(request, error)
        return redirect('cctv:upload_excel')
    _report_saved(request, results, request.user)
    return redirect('cctv:index')
