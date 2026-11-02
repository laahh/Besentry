from django.conf import settings
from django.contrib.gis.db import models

from cctv.models import Cctv, _history


# At module level so Meta's constraints can use them too.
class Tahap(models.TextChoices):
    DRAFT_IT = 'draft_it', 'Draft IT'
    MENUNGGU_SGI = 'menunggu_sgi', 'Menunggu SGI'
    MENUNGGU_SAFETY = 'menunggu_safety', 'Menunggu Safety'
    DISETUJUI = 'disetujui', 'Disetujui'
    DITOLAK = 'ditolak', 'Ditolak'


# A CCTV has at most one request in these stages at a time.
BERJALAN = (Tahap.DRAFT_IT, Tahap.MENUNGGU_SGI, Tahap.MENUNGGU_SAFETY)
# From here on the boundary must be filled in.
BUTUH_BOUNDARY = (Tahap.MENUNGGU_SAFETY, Tahap.DISETUJUI)


class Registrasi(models.Model):
    """One change request for one CCTV, through the three tabs: IT -> SGI -> Safety.

    The boundary SGI draws or uploads stays here as a draft. It is copied to
    Cctv.boundary only when Safety approves, so the live map never shows a draft.
    """

    Tahap = Tahap
    BERJALAN = BERJALAN

    class SumberBoundary(models.TextChoices):
        GAMBAR = 'gambar', 'Digambar di peta'
        UPLOAD = 'upload', 'Upload SHP'

    cctv = models.ForeignKey(Cctv, on_delete=models.PROTECT, related_name='registrasi')
    tahap = models.CharField(max_length=20, choices=Tahap.choices, default=Tahap.DRAFT_IT)

    # Tab 1: IT
    diajukan_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    diajukan = models.DateTimeField(null=True, blank=True, help_text='Saat IT mengirim ke SGI.')
    catatan_it = models.TextField('Catatan IT', blank=True)

    # Tab 2: SGI
    boundary = models.MultiPolygonField(srid=4326, null=True, blank=True)
    sumber_boundary = models.CharField(max_length=10, choices=SumberBoundary.choices, blank=True)
    file_boundary = models.CharField(max_length=255, blank=True, help_text='Nama file SHP yang diupload.')
    sgi_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    sgi_selesai = models.DateTimeField(null=True, blank=True, help_text='Saat SGI mengirim ke Safety.')
    catatan_sgi = models.TextField('Catatan SGI', blank=True)

    # Tab 3: Safety. Sending it back to SGI keeps the reason in catatan_safety.
    safety_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    safety_selesai = models.DateTimeField(null=True, blank=True)
    catatan_safety = models.TextField('Catatan Safety', blank=True)

    dibuat = models.DateTimeField(auto_now_add=True)
    diubah = models.DateTimeField(auto_now=True)

    history = _history(excluded_fields=['dibuat', 'diubah'])

    class Meta:
        ordering = ['-dibuat']
        verbose_name = 'registrasi CCTV'
        verbose_name_plural = 'registrasi CCTV'
        indexes = [models.Index(fields=['tahap'], name='registrasi_tahap')]
        constraints = [
            models.UniqueConstraint(
                fields=['cctv'], condition=models.Q(tahap__in=BERJALAN),
                name='registrasi_satu_berjalan_per_cctv',
            ),
            models.CheckConstraint(
                condition=~models.Q(tahap__in=BUTUH_BOUNDARY) | models.Q(boundary__isnull=False),
                name='registrasi_boundary_sebelum_safety',
            ),
        ]

    def __str__(self):
        return f'{self.cctv} ({self.get_tahap_display()})'

    @property
    def berjalan(self):
        return self.tahap in self.BERJALAN

    @property
    def dikembalikan_safety(self):
        """Back at SGI after Safety sent it back."""
        return self.tahap == self.Tahap.MENUNGGU_SGI and bool(self.catatan_safety)
