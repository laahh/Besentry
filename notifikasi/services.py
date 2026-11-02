"""Tell the SGI PICs when a CCTV is added or moved, so they can (re)draw its coverage.

    snapshot = location_snapshot(cctv)          # before saving an edit
    ...save...
    notify_cctv(cctv, user, before=snapshot)    # new CCTV: before=None

Messages are logged as Notifikasi rows first, then sent on the `notifikasi` queue once the
transaction commits (synchronously unless NOTIFIKASI_ASYNC=True and a worker runs).
"""

import logging

import django_rq
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from . import channels
from .models import Notifikasi, PicSgi

logger = logging.getLogger(__name__)

QUEUE = 'notifikasi'
MOVE_THRESHOLD_M = 1  # GPS jitter below this is not a move
LOCATION_FIELDS = ('site_id', 'lokasi_id', 'detail_lokasi_id', 'lokasi_pemasangan', 'posisi')


def location_snapshot(cctv):
    """The location fields as stored now (call before saving an edit)."""
    row = type(cctv).objects.filter(pk=cctv.pk).select_related('site', 'lokasi', 'detail_lokasi').first()
    if row is None:
        return None
    return {
        'site': row.site.kode if row.site_id else '',
        'lokasi': row.lokasi.nama if row.lokasi_id else '',
        'detail_lokasi': row.detail_lokasi.nama if row.detail_lokasi_id else '',
        'lokasi_pemasangan': row.lokasi_pemasangan,
        'posisi': row.posisi,
        **{field: getattr(row, field) for field in LOCATION_FIELDS if field.endswith('_id')},
    }


def _moved(old, new):
    if old is None or new is None:
        return old != new
    return old.transform(32650, clone=True).distance(new.transform(32650, clone=True)) >= MOVE_THRESHOLD_M


def _coords(point):
    return f'{point.y:.6f}, {point.x:.6f}' if point else '-'


def location_changes(before, cctv):
    """[(label, old, new)] for the location fields that changed."""
    after = location_snapshot(cctv)
    changes = []
    for label, key, id_key in (('Site', 'site', 'site_id'), ('Lokasi', 'lokasi', 'lokasi_id'),
                               ('Detail lokasi', 'detail_lokasi', 'detail_lokasi_id')):
        if before[id_key] != after[id_key]:
            changes.append((label, before[key] or '-', after[key] or '-'))
    if before['lokasi_pemasangan'] != after['lokasi_pemasangan']:
        changes.append(('Lokasi pemasangan', before['lokasi_pemasangan'] or '-', after['lokasi_pemasangan'] or '-'))
    if _moved(before['posisi'], after['posisi']):
        changes.append(('Koordinat', _coords(before['posisi']), _coords(after['posisi'])))
    return changes


def recipients(site_id):
    return PicSgi.objects.filter(aktif=True).filter(Q(site__isnull=True) | Q(site_id=site_id))


def _message(cctv, jenis, user, changes, path=None):
    baru = jenis == Notifikasi.Jenis.CCTV_BARU
    judul = f"[BeSentry] {'CCTV baru' if baru else 'Perubahan lokasi CCTV'}: {cctv.no_cctv}"
    link = settings.SITE_URL.rstrip('/') + (path or reverse('cctv:detail', args=[cctv.pk]))
    lines = [
        f"*{'CCTV baru ditambahkan' if baru else 'Lokasi CCTV berubah'}*",
        '',
        f'No. CCTV: {cctv.no_cctv}',
        f'Nama: {cctv.nama}',
        f'Site / perusahaan: {cctv.site.kode} / {cctv.perusahaan.nama}',
    ]
    if baru:
        lines += [
            f'Lokasi: {cctv.lokasi.nama if cctv.lokasi_id else "-"}'
            f'{" > " + cctv.detail_lokasi.nama if cctv.detail_lokasi_id else ""}',
            f'Lokasi pemasangan: {cctv.lokasi_pemasangan or "-"}',
            f'Koordinat: {_coords(cctv.posisi)}',
        ]
    else:
        lines += ['', 'Perubahan:'] + [f'- {label}: {old} → {new}' for label, old, new in changes]
    if cctv.posisi:
        lines.append(f'Peta: https://maps.google.com/?q={cctv.posisi.y:.6f},{cctv.posisi.x:.6f}')
    who = (user.get_full_name() or user.get_username()) if user else 'Sistem'
    lines += [
        '',
        f'Oleh: {who}, {timezone.localtime():%d %b %Y %H:%M} WITA',
        '',
        'Mohon cek dan digitasi coverage CCTV ini:',
        link,
    ]
    return judul, '\n'.join(lines)


def _queue_rows(rows):
    ids = [row.pk for row in rows]
    if ids:
        transaction.on_commit(lambda: enqueue(ids))
    return rows


def notify_cctv(cctv, user, before=None, changes=None, path=None):
    """New CCTV (before=None) or an edit; only a changed location notifies. Returns the rows made.

    `changes` saves recomputing location_changes(); `path` is where the link in the message goes
    (the SGI digitisation task; the CCTV page by default).
    """
    if before is None:
        jenis, changes = Notifikasi.Jenis.CCTV_BARU, []
    else:
        changes = location_changes(before, cctv) if changes is None else changes
        if not changes:
            return []
        jenis = Notifikasi.Jenis.REPOSISI
    judul, pesan = _message(cctv, jenis, user, changes, path)

    rows = []
    for pic in recipients(cctv.site_id):
        for kanal, tujuan in ((Notifikasi.Kanal.WA, pic.no_wa), (Notifikasi.Kanal.EMAIL, pic.email)):
            if tujuan:
                rows.append(Notifikasi(pic=pic, kanal=kanal, tujuan=tujuan, jenis=jenis, cctv=cctv,
                                       judul=judul, pesan=pesan, dipicu_oleh=user))
    return _queue_rows(Notifikasi.objects.bulk_create(rows))


def notify_test(pic, user):
    """A test message to one PIC on every channel they have."""
    judul = '[BeSentry] Tes notifikasi'
    pesan = (f'Halo {pic.nama}, ini pesan tes dari BeSentry.\n'
             'Notifikasi CCTV baru dan perubahan lokasi CCTV akan dikirim ke nomor/email ini.')
    rows = [
        Notifikasi(pic=pic, kanal=kanal, tujuan=tujuan, jenis=Notifikasi.Jenis.TES, judul=judul, pesan=pesan,
                   dipicu_oleh=user)
        for kanal, tujuan in ((Notifikasi.Kanal.WA, pic.no_wa), (Notifikasi.Kanal.EMAIL, pic.email)) if tujuan
    ]
    return _queue_rows(Notifikasi.objects.bulk_create(rows))


def status_counts(rows):
    statuses = Notifikasi.objects.filter(pk__in=[r.pk for r in rows]).values_list('status', flat=True)
    counts = {status: 0 for status in Notifikasi.Status.values}
    for status in statuses:
        counts[status] += 1
    return counts


def resend(rows):
    rows = list(rows)
    Notifikasi.objects.filter(pk__in=[r.pk for r in rows]).update(status=Notifikasi.Status.MENUNGGU, error='')
    enqueue([r.pk for r in rows])


def enqueue(ids):
    try:
        # commit_mode='auto': we already call this after the commit (see _queue_rows); django-rq's
        # default would defer it to yet another commit, which never comes outside a transaction block.
        django_rq.get_queue(QUEUE, commit_mode='auto').enqueue(send_job, list(ids))
    except Exception:  # Redis down: rows stay "menunggu" and can be resent from the PIC page
        logger.exception('Antrean notifikasi tidak bisa dipakai; %s notifikasi menunggu', len(ids))


def send_job(ids):
    for notifikasi in Notifikasi.objects.filter(pk__in=ids, status=Notifikasi.Status.MENUNGGU):
        deliver(notifikasi)


def deliver(notifikasi):
    notifikasi.percobaan += 1
    try:
        if notifikasi.kanal == Notifikasi.Kanal.WA:
            channels.send_wa(notifikasi.tujuan, notifikasi.pesan)
        else:
            channels.send_email(notifikasi.tujuan, notifikasi.judul, notifikasi.pesan.replace('*', ''))
    except channels.DeliveryError as exc:
        notifikasi.status, notifikasi.error = Notifikasi.Status.GAGAL, str(exc)[:1000]
        logger.warning('Notifikasi %s ke %s gagal: %s', notifikasi.pk, notifikasi.tujuan, exc)
    else:
        notifikasi.status, notifikasi.error, notifikasi.dikirim = Notifikasi.Status.TERKIRIM, '', timezone.now()
    notifikasi.save(update_fields=['status', 'error', 'dikirim', 'percobaan'])
    return notifikasi
