import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import Perusahaan, Site


def normalise_wa(number):
    """'0812-3456 789', '+62812...' or '62812...' -> '62812...' (what Fonnte expects)."""
    digits = re.sub(r'\D', '', number or '')
    if digits.startswith('0'):
        digits = '62' + digits[1:]
    return digits


def _specificity(pic):
    """A company in a site, then a company anywhere, then a whole site, then everything."""
    return 2 * bool(pic.perusahaan_id) + bool(pic.site_id)


def most_specific(pics):
    """Of the PICs covering a place, those closest to it: the others are their backup."""
    pics = list(pics)
    best = max(map(_specificity, pics), default=0)
    return [pic for pic in pics if _specificity(pic) == best]


class Pic(models.Model):
    """Who is in charge of one stage of a request, for a site and perusahaan.

    Mining only verifies work areas; the other stages work on CCTV change requests as well.
    A PIC is told when a request reaches their stage (notifikasi.services) and may work on that
    stage once their employee record has a login (cctv.access.get_scope). An empty site or
    perusahaan means all of them.
    """

    class Tahap(models.TextChoices):
        # Same keys as the tabs of the request page (cctv.pengajuan_views.TABS).
        IT = 'it', 'IT'
        MITRA = 'mitra', 'Survey Mitra'
        SGI = 'sgi', 'Survey BC (SGI)'
        SAFETY = 'safety', 'Safety BC'
        MINING = 'mining', 'Mining (Area Kerja)'

    tahap = models.CharField('Tahap', max_length=10, choices=Tahap.choices)
    # Picked from the employees synced from SID; nama/email/no_wa are copied from it and can be edited.
    karyawan = models.ForeignKey(
        'core.Karyawan', null=True, blank=True, on_delete=models.SET_NULL, related_name='pic',
    )
    nama = models.CharField(max_length=150)
    no_wa = models.CharField('No. WhatsApp', max_length=20, blank=True, help_text='Mis. 081234567890.')
    email = models.EmailField(blank=True)
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.CASCADE, related_name='pic',
        help_text='Kosong = semua site.',
    )
    perusahaan = models.ForeignKey(
        Perusahaan, null=True, blank=True, on_delete=models.CASCADE, related_name='pic',
        help_text='Kosong = semua perusahaan.',
    )
    aktif = models.BooleanField(default=True)
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tahap', 'nama']
        verbose_name = 'PIC'
        verbose_name_plural = 'PIC'
        constraints = [
            models.UniqueConstraint(
                fields=['karyawan', 'tahap', 'site', 'perusahaan'], nulls_distinct=False,
                condition=models.Q(karyawan__isnull=False), name='pic_unik_per_tahap',
            ),
        ]

    def __str__(self):
        return f'{self.nama} ({self.get_tahap_display()})'

    def covers(self, cctv):
        return self.site_id in (None, cctv.site_id) and self.perusahaan_id in (None, cctv.perusahaan_id)

    def clean(self):
        self.no_wa = normalise_wa(self.no_wa)
        if self.no_wa and not 10 <= len(self.no_wa) <= 15:
            raise ValidationError({'no_wa': 'Nomor WhatsApp tidak valid.'})
        if not self.no_wa and not self.email:
            raise ValidationError('Isi minimal No. WhatsApp atau email.')


class Notifikasi(models.Model):
    """One message to one PIC on one channel, kept for audit and resending."""

    class Kanal(models.TextChoices):
        WA = 'wa', 'WhatsApp'
        EMAIL = 'email', 'Email'

    class Jenis(models.TextChoices):
        PENGAJUAN = 'pengajuan', 'Pengajuan perubahan CCTV'
        AREA_KERJA = 'area_kerja', 'Pengajuan area kerja'
        # Sent before the change request flow; kept so older rows still read.
        CCTV_BARU = 'cctv_baru', 'CCTV baru'
        REPOSISI = 'reposisi', 'Perubahan lokasi CCTV'
        TES = 'tes', 'Tes kirim'

    class Status(models.TextChoices):
        MENUNGGU = 'menunggu', 'Menunggu'
        TERKIRIM = 'terkirim', 'Terkirim'
        GAGAL = 'gagal', 'Gagal'

    pic = models.ForeignKey(Pic, null=True, on_delete=models.SET_NULL, related_name='notifikasi')
    kanal = models.CharField(max_length=10, choices=Kanal.choices)
    tujuan = models.CharField(max_length=150, help_text='Nomor WA atau email saat dikirim.')
    jenis = models.CharField(max_length=20, choices=Jenis.choices)
    cctv = models.ForeignKey('cctv.Cctv', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    judul = models.CharField(max_length=200)
    pesan = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.MENUNGGU)
    error = models.TextField(blank=True)
    percobaan = models.PositiveSmallIntegerField(default=0)
    dipicu_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    dibuat = models.DateTimeField(auto_now_add=True)
    dikirim = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-dibuat']
        verbose_name_plural = 'notifikasi'
        indexes = [models.Index(fields=['status', '-dibuat'], name='notifikasi_status')]

    def __str__(self):
        return f'{self.get_kanal_display()} {self.tujuan}: {self.judul}'
