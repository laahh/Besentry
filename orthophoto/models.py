from django.conf import settings
from django.contrib.gis.db import models


class Orthophoto(models.Model):
    """One monthly orthophoto publish. Only the `aktif` one has a COG file on disk."""

    class Status(models.TextChoices):
        MENUNGGU = 'menunggu', 'Menunggu'
        DIPROSES = 'diproses', 'Diproses'
        SIAP = 'siap', 'Siap'
        GAGAL = 'gagal', 'Gagal'
        DIGANTI = 'diganti', 'Diganti'

    BERJALAN = (Status.MENUNGGU, Status.DIPROSES)

    bulan = models.DateField(help_text='Tanggal 1 pada bulan orthophoto.')
    file_sumber = models.CharField(max_length=255, help_text='Nama file mentah di folder incoming.')
    file_cog = models.CharField(max_length=255, blank=True, help_text='Nama file COG di folder cog.')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.MENUNGGU)
    progres = models.PositiveSmallIntegerField(default=0)
    pesan_error = models.TextField(blank=True)
    aktif = models.BooleanField(default=False)
    batas = models.PolygonField(srid=4326, null=True, blank=True)
    zoom_min = models.PositiveSmallIntegerField(null=True, blank=True)
    zoom_maks = models.PositiveSmallIntegerField(null=True, blank=True)
    job_id = models.CharField(max_length=64, blank=True)
    dibuat_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    dibuat = models.DateTimeField(auto_now_add=True)
    selesai = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-dibuat']
        verbose_name_plural = 'orthophoto'
        constraints = [
            models.UniqueConstraint(
                fields=['aktif'], condition=models.Q(aktif=True), name='orthophoto_satu_aktif',
            ),
        ]

    def __str__(self):
        return f'{self.bulan:%Y-%m} ({self.get_status_display()})'

    @property
    def berjalan(self):
        return self.status in self.BERJALAN
