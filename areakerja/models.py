"""Work areas: who works where (MitraSite), and later their weekly areas and CCTV coverage."""

import datetime

from django.conf import settings
from django.contrib.gis.db import models
from django.utils import timezone

from cctv.models import minggu_label
from core.models import Perusahaan, Site


class JenisArea(models.TextChoices):
    """The four kinds of work area, highest priority first: an overlap belongs to the earlier kind."""

    PIT = 'pit', 'Pit'
    HAULING = 'hauling', 'Hauling'
    INFRA_TAMBANG = 'infra_tambang', 'Infra Tambang'
    INFRA_NON_TAMBANG = 'infra_non_tambang', 'Infra Non Tambang'


class MitraSiteQuerySet(models.QuerySet):
    def aktif(self):
        return self.filter(aktif=True)


class MitraSite(models.Model):
    """A company working at a site (PT Berau Coal included): it uploads that site's work areas every week.

    Set inactive in the admin once operations stop there: it then drops out of uploads, coverage and
    the dashboard ("Operasional Sudah Berhenti"). Weeks already locked keep their numbers.
    """

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='mitra_site')
    perusahaan = models.ForeignKey(Perusahaan, on_delete=models.PROTECT, related_name='mitra_site')
    aktif = models.BooleanField(default=True, help_text='Matikan bila operasional di site ini sudah berhenti.')
    catatan = models.TextField(blank=True)
    dibuat = models.DateTimeField(auto_now_add=True)
    diubah = models.DateTimeField(auto_now=True)

    objects = MitraSiteQuerySet.as_manager()

    class Meta:
        ordering = ['site__kode', 'perusahaan__nama']
        verbose_name = 'mitra site'
        verbose_name_plural = 'mitra site'
        constraints = [models.UniqueConstraint(fields=['site', 'perusahaan'], name='mitra_site_unik')]

    def __str__(self):
        return f'{self.site.kode} - {self.perusahaan.nama}'

    @property
    def status_label(self):
        return 'Aktif' if self.aktif else 'Operasional Sudah Berhenti'


class PengajuanAreaKerja(models.Model):
    """One Mitra Site's work areas for one week (Monday to Sunday, WITA): four slots, one per kind.

    Mitra: Draft -> Waiting Survey BC -> Approved, or Return to Mitra and again. PT Berau Coal:
    Survey BC uploads and it is Approved at once; it may still be edited during its week (logged).
    Only one request per Mitra Site and week, so a week is Approved at most once.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        MENUNGGU_SGI = 'menunggu_sgi', 'Waiting Survey BC'
        RETURN_MITRA = 'return_mitra', 'Return to Mitra'
        APPROVED = 'approved', 'Approved'

    BADGE = {
        Status.DRAFT: 'light', Status.MENUNGGU_SGI: 'info', Status.RETURN_MITRA: 'warning', Status.APPROVED: 'success',
    }

    mitra_site = models.ForeignKey(MitraSite, on_delete=models.PROTECT, related_name='pengajuan_area')
    minggu = models.DateField(help_text='Hari Senin pada week yang diajukan.')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    catatan = models.TextField('Catatan pengaju', blank=True)
    diajukan_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    diajukan_pada = models.DateTimeField(null=True, blank=True)
    disetujui_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    disetujui_pada = models.DateTimeField(null=True, blank=True)
    dibuat = models.DateTimeField(auto_now_add=True)
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-minggu', 'mitra_site__site__kode']
        verbose_name = 'pengajuan area kerja'
        verbose_name_plural = 'pengajuan area kerja'
        constraints = [models.UniqueConstraint(fields=['mitra_site', 'minggu'], name='area_kerja_satu_per_minggu')]
        indexes = [models.Index(fields=['status', '-minggu'], name='area_kerja_status')]

    def __str__(self):
        return f'{self.mitra_site} {self.minggu_label}'

    @property
    def minggu_label(self):
        return minggu_label(self.minggu)

    @property
    def badge(self):
        return self.BADGE[self.status]

    @property
    def gabung(self):
        """PT Berau Coal: Survey BC uploads and validates its own areas, no verification step."""
        return self.mitra_site.perusahaan.is_berau_coal

    @property
    def terkunci_minggu(self):
        """Monday 00:00 WITA after this week: when the week closes."""
        return self.minggu + datetime.timedelta(days=7)

    @property
    def terlambat(self):
        """Approved after its week closed: it still counts for the week, with a note."""
        return bool(self.disetujui_pada) and timezone.localdate(self.disetujui_pada) >= self.terkunci_minggu

    def slot(self, jenis):
        return next((area for area in self.area.all() if area.jenis == jenis), None)


class AreaKerja(models.Model):
    """One kind of work area inside a request. `geom` is after the priority clip (no overlap with a
    higher kind); `geom_upload` is as uploaded, dissolved, before the clip."""

    class Sumber(models.TextChoices):
        UPLOAD = 'upload', 'Upload SHP'
        MINGGU_LALU = 'minggu_lalu', 'Gunakan area minggu lalu'
        TIDAK_ADA = 'tidak_ada', 'Tidak ada area jenis ini'

    pengajuan = models.ForeignKey(PengajuanAreaKerja, on_delete=models.CASCADE, related_name='area')
    jenis = models.CharField(max_length=20, choices=JenisArea.choices)
    sumber = models.CharField(max_length=20, choices=Sumber.choices)
    file_shp = models.CharField(max_length=255, blank=True)
    jumlah_feature = models.PositiveIntegerField(default=0)
    minggu_asal = models.DateField(null=True, blank=True, help_text='Week area yang disalin (Gunakan area minggu lalu).')
    geom_upload = models.MultiPolygonField(srid=4326, null=True, blank=True)
    geom = models.MultiPolygonField(srid=4326, null=True, blank=True)
    luas_ha = models.FloatField(null=True, blank=True)
    dipotong_ha = models.FloatField(default=0, help_text='Luas yang dipotong karena tumpang tindih dengan jenis berprioritas lebih tinggi.')
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['pengajuan', 'jenis']
        verbose_name = 'area kerja'
        verbose_name_plural = 'area kerja'
        constraints = [models.UniqueConstraint(fields=['pengajuan', 'jenis'], name='area_kerja_satu_per_jenis')]

    def __str__(self):
        return f'{self.pengajuan} {self.get_jenis_display()}'


class RiwayatAreaKerja(models.Model):
    class Aksi(models.TextChoices):
        DRAFT = 'draft', 'Draft disimpan'
        DIAJUKAN = 'diajukan', 'Diajukan ke Survey BC'
        DISETUJUI = 'disetujui', 'Disetujui Survey BC'
        DIKEMBALIKAN = 'dikembalikan', 'Dikembalikan ke Mitra'
        DIUBAH = 'diubah', 'Area diubah setelah berlaku'

    pengajuan = models.ForeignKey(PengajuanAreaKerja, on_delete=models.CASCADE, related_name='riwayat')
    aksi = models.CharField(max_length=20, choices=Aksi.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    catatan = models.TextField(blank=True)
    # For DIUBAH: the area before the edit, {jenis, sumber, luas_ha, geom (GeoJSON)}.
    sebelum = models.JSONField(default=dict, blank=True)
    waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['waktu', 'pk']
        verbose_name = 'riwayat area kerja'
        verbose_name_plural = 'riwayat area kerja'
