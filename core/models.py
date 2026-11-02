from django.conf import settings
from django.contrib.gis.db import models


class Zona(models.Model):
    nama = models.CharField(max_length=100)
    geom = models.MultiPolygonField(srid=4326)
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'zona'

    def __str__(self):
        return self.nama


# Master data shared by every module (CCTV, PJA, P2H, ...)

class Site(models.Model):
    kode = models.CharField(max_length=20, unique=True, help_text='Mis. LMO, BMO 2, GMO.')
    nama = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['kode']

    def __str__(self):
        return self.kode


class Perusahaan(models.Model):
    nama = models.CharField(max_length=150, unique=True)
    kode = models.CharField(max_length=20, blank=True, help_text='Kode di No. CCTV, mis. BC, PM, BM.')
    sid_id = models.IntegerField('ID di SID', null=True, blank=True, unique=True,
                                 help_text='companyId di API SID, diisi oleh sinkronisasi.')

    class Meta:
        ordering = ['nama']
        verbose_name_plural = 'perusahaan'

    def __str__(self):
        return self.nama


class Karyawan(models.Model):
    """Employee data synced from the SID API. Linked to a login account once they sign in.

    Personal data the app does not need (KTP, date of birth, RFID, address) is not stored.
    """

    sid = models.CharField('SID', max_length=20, unique=True)
    employee_id = models.IntegerField('ID karyawan di SID', null=True, blank=True, unique=True)
    npk = models.CharField('NPK', max_length=30, blank=True)
    nama = models.CharField(max_length=150)
    email = models.CharField(max_length=150, blank=True)
    # Only in the per-SID detail API (and the BEATS login), not in the bulk employee list.
    no_hp = models.CharField('No. HP', max_length=30, blank=True)
    perusahaan = models.ForeignKey(
        Perusahaan, null=True, blank=True, on_delete=models.SET_NULL, related_name='karyawan',
    )
    departemen = models.CharField(max_length=150, blank=True)
    jabatan_struktural = models.CharField(max_length=150, blank=True)
    jabatan_fungsional = models.CharField(max_length=100, blank=True)
    site = models.CharField('Site dedicated', max_length=100, blank=True)
    status = models.CharField(max_length=30, blank=True, help_text='Status di SID, mis. AKTIF / NONAKTIF.')
    status_permit = models.CharField(max_length=30, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='karyawan',
    )
    disinkron = models.DateTimeField('Terakhir disinkron', null=True, blank=True)

    STATUS_AKTIF = 'AKTIF'

    class Meta:
        ordering = ['nama']
        verbose_name_plural = 'karyawan'

    def __str__(self):
        return f'{self.sid} - {self.nama}'

    @property
    def aktif(self):
        # An empty status means it was never synced in detail; treat as active.
        return self.status in ('', self.STATUS_AKTIF)


class Lokasi(models.Model):
    """Location master. The id keeps the value from the source system (e.g. 1172174953)."""

    nama = models.CharField(max_length=200)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name='lokasi')

    class Meta:
        ordering = ['nama']
        verbose_name_plural = 'lokasi'

    def __str__(self):
        return self.nama


class DetailLokasi(models.Model):
    lokasi = models.ForeignKey(Lokasi, on_delete=models.PROTECT, related_name='detail')
    nama = models.CharField(max_length=200)

    class Meta:
        ordering = ['nama']
        verbose_name = 'detail lokasi'
        verbose_name_plural = 'detail lokasi'

    def __str__(self):
        return self.nama
