"""Tell whoever works on the next stage of a CCTV change request, by WhatsApp and email.

    notify_pengajuan(pengajuan, user)   # after every step of the flow (cctv.coverage)

The master PIC (notifikasi.models.Pic) says who that is: the stage's PICs whose site and perusahaan
match the CCTV. A stage nobody is assigned to falls back to the users holding its role, so no
request goes unnoticed. On Return to IT, the IT user who submitted is always told as well. Once the
request is approved, everyone involved is told: every stage, and the IT user who submitted.

Messages are logged as Notifikasi rows first, then sent on the `notifikasi` queue once the
transaction commits (synchronously unless NOTIFIKASI_ASYNC=True and a worker runs).
"""

import logging

import django_rq
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from navigation import roles

from . import channels
from .models import Notifikasi, Pic, normalise_wa

logger = logging.getLogger(__name__)

QUEUE = 'notifikasi'
STATUS_TAHAP = {
    'menunggu_mitra': Pic.Tahap.MITRA,
    'return_mitra': Pic.Tahap.MITRA,
    'menunggu_sgi': Pic.Tahap.SGI,
    'return_sgi': Pic.Tahap.SGI,
    'menunggu_safety': Pic.Tahap.SAFETY,
    'return_it': Pic.Tahap.IT,
}
FINISHED = ('approved', 'approved_warning')
BATCH_LISTED = 30  # CCTV numbers named in one message; WhatsApp messages should stay readable


def pics_for(cctv, tahap=None):
    """The active PICs covering this CCTV's site and perusahaan, of one stage or of all."""
    pics = Pic.objects.filter(aktif=True).filter(
        Q(site__isnull=True) | Q(site_id=cctv.site_id),
        Q(perusahaan__isnull=True) | Q(perusahaan_id=cctv.perusahaan_id),
    )
    return pics.filter(tahap=tahap) if tahap else pics


def _users(role):
    return get_user_model().objects.filter(is_active=True, groups__name=role).select_related('karyawan').distinct()


def _contacts(user):
    """(WhatsApp number, email) of a user, from their account and the synced employee record."""
    karyawan = getattr(user, 'karyawan', None)
    wa = normalise_wa(karyawan.no_hp) if karyawan and karyawan.no_hp else ''
    email = user.email or (karyawan.email.lower() if karyawan and karyawan.email else '')
    return wa, email


def recipients(pengajuan):
    """[(Pic or None, WhatsApp, email)] for the stage the request just reached, or all stages when done."""
    finished = pengajuan.status in FINISHED
    stages = Pic.Tahap.values if finished else [STATUS_TAHAP[pengajuan.status]]
    pics, users = [], []
    for tahap in stages:
        stage_pics = list(pics_for(pengajuan.cctv, tahap))
        pics += stage_pics
        if not stage_pics:
            users += _role_users(tahap, pengajuan.cctv)
    if (finished or stages == [Pic.Tahap.IT]) and pengajuan.diajukan_oleh_id:
        users.append(pengajuan.diajukan_oleh)
    # PICs first: someone reached both ways is logged under their PIC row.
    return [(pic, pic.no_wa, pic.email) for pic in pics] + [(None, *_contacts(user)) for user in users]


def _role_users(tahap, cctv):
    """Nobody assigned to the stage: the users holding its role."""
    from cctv.access import get_scope
    if tahap == Pic.Tahap.MITRA:
        return [user for user in _users(roles.SURVEY_MITRA) if get_scope(user).can_digitize(cctv)]
    if tahap == Pic.Tahap.SGI:
        return list(_users(roles.SURVEY_BC))
    if tahap == Pic.Tahap.SAFETY:
        return list(_users(roles.SAFETY))
    return []


TODO = {
    'menunggu_mitra': 'Mohon digitasi boundary coverage CCTV ini (gambar di peta atau upload SHP):',
    'return_mitra': 'Boundary dikembalikan ke Survey Mitra. Mohon perbaiki:',
    'menunggu_sgi': 'Mohon review boundary coverage CCTV ini:',
    'return_sgi': 'Safety mengembalikan ke Survey BC. Mohon review ulang:',
    'menunggu_safety': 'Mohon review dan beri keputusan Safety:',
    'return_it': 'Pengajuan dikembalikan ke IT. Mohon perbaiki data CCTV:',
    'approved': 'Pengajuan selesai, boundary sudah sah dan berlaku:',
    'approved_warning': 'Pengajuan selesai dengan catatan, boundary sudah berlaku:',
}
# PT Berau Coal: Survey BC digitises and verifies in one step.
TODO_GABUNG = {
    'menunggu_sgi': 'Mohon digitasi dan verifikasi boundary coverage CCTV ini (gambar di peta atau upload SHP):',
    'return_sgi': 'Safety mengembalikan ke Survey BC. Mohon perbaiki boundary:',
}


def _message(pengajuan, user):
    cctv = pengajuan.cctv
    status = pengajuan.get_status_display()
    judul = f'[BeSentry] {status}: {cctv.no_cctv}'
    link = settings.SITE_URL.rstrip('/') + reverse('cctv:pengajuan', args=[pengajuan.pk])
    lines = [
        f'*{status}*',
        '',
        f'No. CCTV: {cctv.no_cctv}',
        f'Nama: {cctv.nama}',
        f'Site / perusahaan: {cctv.site.kode} / {cctv.perusahaan.nama}',
        f'Pengajuan: {pengajuan.get_jenis_display()} · {pengajuan.minggu_label}',
    ]
    if pengajuan.perubahan:
        lines += ['', 'Perubahan:'] + [f'- {label}: {old} → {new}' for label, old, new in pengajuan.perubahan]
    last = pengajuan.riwayat.filter(aksi='dikembalikan').order_by('-waktu').first()
    if pengajuan.status.startswith('return') and last:
        lines += ['', f'Catatan: {last.catatan}']
    if pengajuan.status in FINISHED:
        lines += ['', f'Feedback Safety: {pengajuan.feedback_safety}']
        if pengajuan.status == 'approved_warning':
            lines.append('Belum terpenuhi: ' + '; '.join(pengajuan.checklist_safety_kurang))
    if cctv.posisi:
        lines.append(f'Peta: https://maps.google.com/?q={cctv.posisi.y:.6f},{cctv.posisi.x:.6f}')
    who = (user.get_full_name() or user.get_username()) if user else 'Sistem'
    lines += [
        '',
        f'Oleh: {who}, {timezone.localtime():%d %b %Y %H:%M} WITA',
        '',
        (pengajuan.gabung and TODO_GABUNG.get(pengajuan.status)) or TODO.get(pengajuan.status, 'Detail pengajuan:'),
        link,
    ]
    return judul, '\n'.join(lines)


def _queue_rows(rows):
    ids = [row.pk for row in rows]
    if ids:
        transaction.on_commit(lambda: enqueue(ids))
    return rows


def notify_pengajuan(pengajuan, user):
    """Messages for the stage `pengajuan` just reached; returns the rows made (none when it is done)."""
    if pengajuan.status not in TODO:
        return []
    judul, pesan = _message(pengajuan, user)
    rows, seen = [], set()
    for pic, wa, email in recipients(pengajuan):
        for kanal, tujuan in ((Notifikasi.Kanal.WA, wa), (Notifikasi.Kanal.EMAIL, email)):
            if tujuan and (kanal, tujuan) not in seen:  # one message per number/address
                seen.add((kanal, tujuan))
                rows.append(Notifikasi(pic=pic, kanal=kanal, tujuan=tujuan, jenis=Notifikasi.Jenis.PENGAJUAN,
                                       cctv=pengajuan.cctv, judul=judul, pesan=pesan, dipicu_oleh=user))
    return _queue_rows(Notifikasi.objects.bulk_create(rows))


def notify_batch(pengajuans, user):
    """One message per person for requests that reached a stage together, not one per request."""
    targets = {}  # (kanal, tujuan) -> (Pic or None, CCTV numbers)
    for pengajuan in pengajuans:
        for pic, wa, email in recipients(pengajuan):
            for kanal, tujuan in ((Notifikasi.Kanal.WA, wa), (Notifikasi.Kanal.EMAIL, email)):
                if tujuan:
                    targets.setdefault((kanal, tujuan), (pic, []))[1].append(pengajuan.cctv.no_cctv)
    status = pengajuans[0].get_status_display()
    rows = []
    for (kanal, tujuan), (pic, numbers) in targets.items():
        judul = f'[BeSentry] {status}: {len(numbers)} CCTV'
        rows.append(Notifikasi(pic=pic, kanal=kanal, tujuan=tujuan, jenis=Notifikasi.Jenis.PENGAJUAN, judul=judul,
                               pesan=_batch_message(pengajuans[0], numbers, user), dipicu_oleh=user))
    return _queue_rows(Notifikasi.objects.bulk_create(rows))


def _batch_message(pengajuan, numbers, user):
    listed = [f'- {number}' for number in numbers[:BATCH_LISTED]]
    if len(numbers) > BATCH_LISTED:
        listed.append(f'- dan {len(numbers) - BATCH_LISTED} CCTV lainnya')
    who = user.get_full_name() or user.get_username()
    return '\n'.join([
        f'*{pengajuan.get_status_display()}: {len(numbers)} CCTV*',
        '',
        *listed,
        '',
        f'Oleh: {who}, {timezone.localtime():%d %b %Y %H:%M} WITA',
        '',
        TODO.get(pengajuan.status, 'Detail pengajuan:'),
        settings.SITE_URL.rstrip('/') + reverse('cctv:tasklist'),
    ])


def notify_test(pic, user):
    """A test message to one PIC on every channel they have."""
    judul = '[BeSentry] Tes notifikasi'
    pesan = (f'Halo {pic.nama}, ini pesan tes dari BeSentry.\n'
             f'Notifikasi pengajuan perubahan CCTV tahap {pic.get_tahap_display()} akan dikirim ke nomor/email ini.')
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
