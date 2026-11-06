"""Tell the people concerned at every step of a work-area request, by WhatsApp and email.

    Sent to Verifikasi BC     SGI, Mining and Safety BC
    One department approved   the departments still waited for, and whoever sent the request
    Sent back                 the uploaders of the Mitra Site, and whoever sent the request
    Approved                  all of the above

The master PIC says who a stage is; a stage nobody covers falls back to the users holding its role.
A message that cannot be made is logged and never stops the step itself.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from areakerja import access
from areakerja.models import Departemen, RiwayatAreaKerja
from cctv.access import get_scope

from . import services
from .models import Notifikasi

logger = logging.getLogger(__name__)
Aksi = RiwayatAreaKerja.Aksi

TODO = {
    Aksi.DIAJUKAN: 'Mohon verifikasi area kerja ini:',
    Aksi.VERIFIKASI: 'Detail pengajuan area kerja:',
    Aksi.DIKEMBALIKAN: 'Mohon perbaiki area kerja, lalu ajukan lagi:',
    Aksi.DISETUJUI: 'Area kerja sudah sah dan berlaku:',
}


def _pending(pengajuan):
    done = set(pengajuan.verifikasi.values_list('departemen', flat=True))
    return [department for department in Departemen if department.value not in done]


def _stage(tahap, role, place, allowed=None):
    """(PICs, users): the PICs of the stage covering this place, else the holders of its role."""
    pics = list(services.pics_for(place, tahap))
    if pics:
        return pics, []
    return [], [user for user in services._users(role) if allowed is None or allowed(user)]


def _departments(departments, place):
    stages = [_stage(*access.DEPARTEMEN_OWNER[department], place) for department in departments]
    return [pic for pics, _ in stages for pic in pics], [user for _, users in stages for user in users]


def _uploaders(place):
    return _stage(*access.uploader(place), place, lambda user: access.can_upload(get_scope(user), place))


def recipients(pengajuan, aksi):
    """[(Pic or None, WhatsApp, email)] for one step. PICs first: someone reached both ways stays a PIC."""
    place = pengajuan.mitra_site
    if aksi == Aksi.DIAJUKAN:
        pics, users = _departments(Departemen, place)
    elif aksi == Aksi.VERIFIKASI:
        pics, users = _departments(_pending(pengajuan), place)
    elif aksi == Aksi.DIKEMBALIKAN:
        pics, users = _uploaders(place)
    else:
        department_pics, department_users = _departments(Departemen, place)
        uploader_pics, uploader_users = _uploaders(place)
        pics, users = department_pics + uploader_pics, department_users + uploader_users
    if aksi != Aksi.DIAJUKAN and pengajuan.diajukan_oleh_id:
        users = [*users, pengajuan.diajukan_oleh]
    return [(pic, pic.no_wa, pic.email) for pic in pics] + [(None, *services._contacts(user)) for user in users]


def _nama(pengajuan):
    place = pengajuan.mitra_site
    return f'{place.site.kode} - {place.perusahaan.nama} {pengajuan.minggu_label}'


def _judul(pengajuan, aksi, departemen):
    return f'{departemen} menyetujui' if aksi == Aksi.VERIFIKASI else pengajuan.status_label


def _verification_lines(pengajuan):
    waiting = _pending(pengajuan)
    done = [department.label for department in Departemen if department not in waiting]
    lines = [f'Sudah setuju: {", ".join(done)}'] if done else []
    return lines + [f'Menunggu: {", ".join(department.label for department in waiting)}']


def _message(pengajuan, user, aksi, departemen):
    judul = _judul(pengajuan, aksi, departemen)
    place = pengajuan.mitra_site
    luas = sum(area.luas_ha or 0 for area in pengajuan.area.all())
    lines = [
        f'*{judul}*',
        '',
        f'Mitra Site: {place.site.kode} - {place.perusahaan.nama}',
        f'Week: {pengajuan.minggu_label}',
        f'Luas area kerja: {luas:,.2f} ha',
    ]
    if aksi in (Aksi.DIAJUKAN, Aksi.VERIFIKASI):
        lines += ['', *_verification_lines(pengajuan)]
    if aksi == Aksi.DIAJUKAN and pengajuan.catatan:
        lines += ['', f'Catatan pengaju: {pengajuan.catatan}']
    if aksi == Aksi.DIKEMBALIKAN:
        last = pengajuan.riwayat.filter(aksi=Aksi.DIKEMBALIKAN).order_by('-waktu', '-pk').first()
        lines += ['', f'Catatan: {last.catatan}'] if last else []
    who = (user.get_full_name() or user.get_username()) if user else 'Sistem'
    lines += [
        '',
        f'Oleh: {who}, {timezone.localtime():%d %b %Y %H:%M} WITA',
        '',
        TODO[aksi],
        settings.SITE_URL.rstrip('/') + reverse('areakerja:pengajuan', args=[pengajuan.pk]),
    ]
    return f'[BeSentry] {judul}: {_nama(pengajuan)}', '\n'.join(lines)


def _create_rows(pengajuan, user, aksi, departemen):
    judul, pesan = _message(pengajuan, user, aksi, departemen)
    rows, seen = [], set()
    for pic, wa, email in recipients(pengajuan, aksi):
        for kanal, tujuan in ((Notifikasi.Kanal.WA, wa), (Notifikasi.Kanal.EMAIL, email)):
            if tujuan and (kanal, tujuan) not in seen:  # one message per number/address
                seen.add((kanal, tujuan))
                rows.append(Notifikasi(pic=pic, kanal=kanal, tujuan=tujuan, jenis=Notifikasi.Jenis.AREA_KERJA,
                                       judul=judul, pesan=pesan, dipicu_oleh=user))
    return services._queue_rows(Notifikasi.objects.bulk_create(rows))


def notify(sender, pengajuan, user, aksi, departemen='', **kwargs):
    """Receiver of area_kerja_berubah. Leaves the rows on the request, for the page to report them."""
    if aksi not in TODO:
        return
    try:
        with transaction.atomic():  # a savepoint: a failure here must not undo the step itself
            pengajuan.notifikasi_terkirim = _create_rows(pengajuan, user, aksi, departemen)
    except Exception:
        logger.exception('Notifikasi area kerja %s (%s) gagal dibuat', pengajuan.pk, aksi)
        pengajuan.notifikasi_terkirim = []
