import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import Site


def normalise_wa(number):
    """'0812-3456 789', '+62812...' or '62812...' -> '62812...' (what Fonnte expects)."""
    digits = re.sub(r'\D', '', number or '')
    if digits.startswith('0'):
        digits = '62' + digits[1:]
    return digits


class PicSgi(models.Model):
    """Who at Survey & Geospatial Information (Survey BC) hears when a request waits for their review."""

    # Picked from the employees synced from SID; nama/email/no_wa are copied from it and can be edited.
    karyawan = models.ForeignKey(
        'core.Karyawan', null=True, blank=True, on_delete=models.SET_NULL, related_name='pic_sgi',
    )
    nama = models.CharField(max_length=150)
    no_wa = models.CharField('No. WhatsApp', max_length=20, blank=True, help_text='Mis. 081234567890.')
    email = models.EmailField(blank=True)
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.CASCADE, related_name='pic_sgi',
        help_text='Kosong = menerima notifikasi semua site.',
    )
    aktif = models.BooleanField(default=True)
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nama']
        verbose_name = 'PIC SGI'
        verbose_name_plural = 'PIC SGI'

    def __str__(self):
        return self.nama

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
        # Sent before the change request flow; kept so older rows still read.
        CCTV_BARU = 'cctv_baru', 'CCTV baru'
        REPOSISI = 'reposisi', 'Perubahan lokasi CCTV'
        TES = 'tes', 'Tes kirim'

    class Status(models.TextChoices):
        MENUNGGU = 'menunggu', 'Menunggu'
        TERKIRIM = 'terkirim', 'Terkirim'
        GAGAL = 'gagal', 'Gagal'

    pic = models.ForeignKey(PicSgi, null=True, on_delete=models.SET_NULL, related_name='notifikasi')
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
