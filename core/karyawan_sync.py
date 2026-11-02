"""Keep Perusahaan, Karyawan and login accounts in step with the SID/BEATS APIs."""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from . import sid_api
from .models import Karyawan, Perusahaan

logger = logging.getLogger(__name__)

KARYAWAN_SYNC_FIELDS = (
    'employee_id', 'npk', 'nama', 'email', 'perusahaan', 'departemen', 'jabatan_struktural',
    'jabatan_fungsional', 'site', 'status', 'status_permit', 'disinkron',
)


def _text(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def _name(obj):
    return (obj or {}).get('name') or ''


def perusahaan_for(company_id, name):
    """The Perusahaan for a SID company: by companyId, else the same name (then linked), else new."""
    if not company_id:
        return None
    perusahaan = Perusahaan.objects.filter(sid_id=company_id).first()
    if perusahaan:
        return perusahaan
    name = _text(name, 150) or f'Perusahaan {company_id}'
    perusahaan = Perusahaan.objects.filter(nama__iexact=name, sid_id__isnull=True).first()
    if perusahaan:
        perusahaan.sid_id = company_id
        perusahaan.save(update_fields=['sid_id'])
        return perusahaan
    if Perusahaan.objects.filter(nama__iexact=name).exists():
        name = f'{name} ({company_id})'[:150]  # same name, different SID company
    return Perusahaan.objects.create(nama=name, sid_id=company_id)


def _detail_values(detail):
    employee = detail.get('employee') or {}
    company = employee.get('company') or {}
    return {
        'employee_id': employee.get('id'),
        'npk': _text(employee.get('employeeIdNumber'), 30),
        'nama': _text(employee.get('name'), 150),
        'email': _text(employee.get('email'), 150).lower(),
        'perusahaan': perusahaan_for(company.get('id'), company.get('name')),
        'departemen': _text(_name(employee.get('department')), 150),
        'jabatan_struktural': _text(_name(employee.get('structuralPosition')), 150),
        'jabatan_fungsional': _text(_name(employee.get('functionalPosition')), 100),
        'site': _text(_name(detail.get('dedicatedSite')), 100),
        'status': _text(_name(employee.get('status')), 30).upper(),
        'status_permit': _text(employee.get('statusPermit'), 30),
    }


def _detail_is_stale(sid):
    synced = Karyawan.objects.filter(sid=sid).values_list('disinkron', flat=True).first()
    max_age = timedelta(hours=settings.SID_DETAIL_MAX_AGE_HOURS)
    return synced is None or timezone.now() - synced > max_age


def login_user(account, password):
    """Create or update the local account for a successful BEATS login.

    The password hash is kept locally so people can still sign in while the API is down
    (see BEATS_OFFLINE_FALLBACK). Returns (user, karyawan).
    """
    sid = account['username']
    detail = None
    if settings.SID_API_KEY and _detail_is_stale(sid):
        try:
            detail = sid_api.employee_detail(sid)
        except sid_api.ApiUnavailable:
            detail = None  # login still works; the detail is refreshed next time

    User = get_user_model()
    with transaction.atomic():
        user, _ = User.objects.get_or_create(username=sid)
        nama = _text(account.get('namaKaryawan'), 150)
        email = _text(account.get('email'), 150).lower()
        changed = []
        if not user.check_password(password):
            user.set_password(password)
            changed.append('password')
        for attr, value in (('first_name', nama), ('email', email)):
            if value and getattr(user, attr) != value:
                setattr(user, attr, value)
                changed.append(attr)
        if changed:
            user.save(update_fields=changed)

        karyawan = Karyawan.objects.filter(sid=sid).first() or Karyawan(sid=sid)
        karyawan.user = user
        karyawan.nama = nama or karyawan.nama or sid
        karyawan.email = email or karyawan.email
        karyawan.employee_id = karyawan.employee_id or _int(account.get('idKaryawan'))
        perusahaan = perusahaan_for(_int(account.get('idPerusahaan')), account.get('namaPerusahaan'))
        karyawan.perusahaan = perusahaan or karyawan.perusahaan
        if detail:
            for name, value in _detail_values(detail).items():
                if value not in ('', None):
                    setattr(karyawan, name, value)
            karyawan.disinkron = timezone.now()
        karyawan.save()
    return user, karyawan


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Bulk sync (python manage.py sync_karyawan)

def sync_companies():
    """Upsert every SID company into Perusahaan. Returns the number of companies seen."""
    count = 0
    for company in sid_api.companies():
        perusahaan_for(company.get('companyId'), company.get('name'))
        count += 1
    return count


@dataclass
class EmployeeSyncReport:
    karyawan: int = 0
    dinonaktifkan: list = field(default_factory=list)


def _list_values(row, perusahaan, now):
    return {
        'employee_id': row.get('employeeId'),
        'npk': _text(row.get('npk'), 30),
        'nama': _text(row.get('name'), 150) or row['sidCode'],
        'email': _text(row.get('email'), 150).lower(),
        'perusahaan': perusahaan,
        'departemen': _text(row.get('departmentName'), 150),
        'jabatan_struktural': _text(row.get('structuralPosition'), 150),
        'jabatan_fungsional': _text(row.get('functionalPosition'), 100),
        'site': _text(row.get('dedicatedSite'), 100),
        'status': _text(row.get('status'), 30).upper(),
        'status_permit': _text(row.get('statusPermit'), 30),
        'disinkron': now,
    }


def sync_employees(perusahaan, batch_size=2000):
    """Upsert the employees of one Perusahaan (must have sid_id) in bulk, then switch off
    the login of anyone SID no longer lists as AKTIF."""
    now = timezone.now()
    report = EmployeeSyncReport()
    rows = {}
    for row in sid_api.employees(perusahaan.sid_id):
        sid = _text(row.get('sidCode'), 20)
        if sid:
            rows[sid] = Karyawan(sid=sid, **_list_values(row, perusahaan, now))

    with transaction.atomic():
        # employee_id is unique too; drop it from rows that would collide with another SID.
        taken = dict(
            Karyawan.objects.filter(employee_id__in=[k.employee_id for k in rows.values() if k.employee_id])
            .values_list('employee_id', 'sid')
        )
        for karyawan in rows.values():
            if karyawan.employee_id in taken and taken[karyawan.employee_id] != karyawan.sid:
                karyawan.employee_id = None
        Karyawan.objects.bulk_create(
            rows.values(), batch_size=batch_size,
            update_conflicts=True, unique_fields=['sid'], update_fields=KARYAWAN_SYNC_FIELDS,
        )
        report.karyawan = len(rows)

        nonaktif = [sid for sid, karyawan in rows.items() if not karyawan.aktif]
        users = get_user_model().objects.filter(
            karyawan__sid__in=nonaktif, is_active=True, is_superuser=False,
        )
        report.dinonaktifkan = list(users.values_list('username', flat=True))
        users.update(is_active=False)
    return report
