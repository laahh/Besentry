import hashlib

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.gis.db import models
from django.urls import reverse
from simple_history.models import HistoricalRecords

from core.fields import EncryptedTextField
from core.models import DetailLokasi, Lokasi, Perusahaan, Site


def _get_history_user(historical):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(pk=historical.history_user_id).first()


def _set_history_user(historical, user):
    historical.history_user_id = getattr(user, 'pk', None)


def _history(**kwargs):
    # History rows live in the `log` database (see config/db_router.py). The user is
    # kept as a plain id: a real foreign key would make deleting a user cascade into
    # a table that does not exist in the main database.
    return HistoricalRecords(
        app='cctv_log',
        history_user_id_field=models.IntegerField(null=True, blank=True, db_index=True),
        history_user_getter=_get_history_user,
        history_user_setter=_set_history_user,
        **kwargs,
    )


class ControlRoom(models.Model):
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='control_room')
    nama = models.CharField(max_length=150)

    class Meta:
        ordering = ['site__kode', 'nama']
        constraints = [models.UniqueConstraint(fields=['site', 'nama'], name='control_room_unik_per_site')]

    def __str__(self):
        return f'{self.nama} ({self.site})'


class Cctv(models.Model):
    class Kategori(models.TextChoices):
        SUPPORT = 'support', 'CCTV Support'
        MINING_EYES = 'mining_eyes', 'Mining Eyes'
        PORTABLE = 'portable', 'CCTV Portable'

    class Fungsi(models.TextChoices):
        FIXED = 'fixed', 'Fixed View'
        PATROL = 'patrol', 'Patrol View'
        MOBILE = 'mobile', 'Mobile View'
        PORTABLE = 'portable', 'Portable (IKK)'

    class Bentuk(models.TextChoices):
        STATIS = 'statis', 'Statis'
        DINAMIS = 'dinamis', 'Dinamis'

    class Jenis(models.TextChoices):
        FIXED = 'fixed', 'FIXED'
        PTZ = 'ptz', 'PTZ'
        PTZ_FIXED = 'ptz_fixed', 'PTZ & FIXED'
        MOBILE = 'mobile', 'MOBILE'
        PORTABLE = 'portable', 'PORTABLE'

    class Kekritisan(models.TextChoices):
        NON_KRITIS = 'non_kritis', 'Non Kritis'
        KRITIS = 'kritis', 'Kritis'
        HIGHRISK = 'highrisk', 'Highrisk'

    class Status(models.TextChoices):
        LIVE = 'live', 'Live View'
        POST_EVENT = 'post_event', 'Post Event'
        OFFLINE = 'offline', 'Offline'

    class Kondisi(models.TextChoices):
        BAIK = 'baik', 'Baik'
        BREAKDOWN = 'breakdown', 'Breakdown'
        REPAIR = 'repair', 'Repair'
        DISMANTLE = 'dismantle', 'Dismantle'
        TAKE_OUT = 'take_out', 'Take Out'

    no_cctv = models.CharField('No. CCTV', max_length=40, unique=True)
    nama = models.CharField('Nama CCTV', max_length=150)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='cctv')
    perusahaan = models.ForeignKey(Perusahaan, on_delete=models.PROTECT, related_name='cctv')
    kategori = models.CharField(max_length=20, choices=Kategori.choices, blank=True)
    fungsi = models.CharField('Fungsi CCTV', max_length=20, choices=Fungsi.choices, blank=True)
    bentuk_instalasi = models.CharField(max_length=20, choices=Bentuk.choices, blank=True)
    jenis = models.CharField(max_length=20, choices=Jenis.choices, blank=True)
    lokasi_pemasangan = models.CharField(max_length=200, blank=True)
    control_room = models.ForeignKey(
        ControlRoom, null=True, blank=True, on_delete=models.SET_NULL, related_name='cctv',
    )
    lokasi = models.ForeignKey(Lokasi, null=True, blank=True, on_delete=models.SET_NULL, related_name='cctv')
    detail_lokasi = models.ForeignKey(
        DetailLokasi, null=True, blank=True, on_delete=models.SET_NULL, related_name='cctv',
    )
    kategori_area = models.CharField(
        'Kategori area tercapture', max_length=20, choices=Kekritisan.choices, blank=True,
    )
    kategori_aktivitas = models.CharField(
        'Kategori aktivitas tercapture', max_length=20, choices=Kekritisan.choices, blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.LIVE)
    kondisi = models.CharField(max_length=20, choices=Kondisi.choices, default=Kondisi.BAIK)
    posisi = models.PointField(srid=4326, null=True, blank=True)
    connected = models.BooleanField(default=False)
    mirrored = models.BooleanField(default=False)
    auto_alert = models.BooleanField('Fitur auto alert', default=False)
    keterangan = models.TextField(blank=True)
    verifikasi_ocr = models.CharField('Verifikasi petugas OCR', max_length=255, blank=True)
    periode_update = models.DateField(null=True, blank=True, help_text='Tanggal 1 pada bulan update.')
    dibuat = models.DateTimeField(auto_now_add=True)
    diubah = models.DateTimeField(auto_now=True)

    history = _history(excluded_fields=['dibuat', 'diubah'])

    class Meta:
        ordering = ['no_cctv']
        verbose_name = 'CCTV'
        verbose_name_plural = 'CCTV'
        indexes = [
            models.Index(fields=['site', 'kondisi'], name='cctv_site_kondisi'),
            models.Index(fields=['status'], name='cctv_status'),
        ]

    def __str__(self):
        return f'{self.no_cctv} - {self.nama}'

    KONDISI_BADGE = {
        Kondisi.BAIK: 'success',
        Kondisi.BREAKDOWN: 'danger',
        Kondisi.REPAIR: 'warning',
        Kondisi.DISMANTLE: 'dark',
        Kondisi.TAKE_OUT: 'light',
    }

    @property
    def kondisi_badge(self):
        return self.KONDISI_BADGE.get(self.kondisi, 'light')

    STATUS_BADGE = {Status.LIVE: 'success', Status.POST_EVENT: 'info', Status.OFFLINE: 'danger'}

    @property
    def status_badge(self):
        return self.STATUS_BADGE.get(self.status, 'light')


class CctvSpec(models.Model):
    """Device and installation spec. Position stays on Cctv.posisi."""

    cctv = models.OneToOneField(Cctv, primary_key=True, on_delete=models.CASCADE, related_name='spec')
    brand = models.CharField(max_length=50, blank=True)
    model = models.CharField('Model / spesifikasi zoom', max_length=150, blank=True)
    radius_m = models.FloatField('Radius pengawasan (m)', null=True, blank=True)
    purpose = models.CharField(max_length=100, blank=True)
    tinggi_pasang_m = models.FloatField('Tinggi pasang (m)', null=True, blank=True)
    tilt_deg = models.FloatField('Tilt (°)', null=True, blank=True)
    azimuth_deg = models.FloatField('Azimuth (°)', null=True, blank=True)

    history = _history()

    class Meta:
        verbose_name = 'spesifikasi CCTV'
        verbose_name_plural = 'spesifikasi CCTV'

    def __str__(self):
        return f'Spec {self.cctv_id}'


class CctvAkses(models.Model):
    """Connection details. Only IT roles may see this; the password is encrypted."""

    cctv = models.OneToOneField(Cctv, primary_key=True, on_delete=models.CASCADE, related_name='akses')
    nvr = models.CharField('Group / nama NVR', max_length=100, blank=True)
    channel = models.CharField('No. channel', max_length=20, blank=True)
    ip_cctv = models.CharField('IP CCTV', max_length=255, blank=True)
    ip_nvr = models.CharField('IP NVR', max_length=255, blank=True)
    link_akses = models.CharField(max_length=500, blank=True)
    username = models.CharField(max_length=150, blank=True)
    password = EncryptedTextField(blank=True)
    prtg_name = models.CharField('Nama PRTG', max_length=150, blank=True)

    # Old passwords must not pile up in the log database.
    history = _history(excluded_fields=['password'])

    class Meta:
        verbose_name = 'akses CCTV'
        verbose_name_plural = 'akses CCTV'

    def __str__(self):
        return f'Akses {self.cctv_id}'


class CakupanCctv(models.Model):
    """Which CCTV a role (Django group) may see: one site/perusahaan pair per row.

    An empty site or perusahaan means "all". Superusers and admin-hazard-motion see
    everything without any row (see cctv/access.py).
    """

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='cakupan_cctv', verbose_name='role')
    site = models.ForeignKey(
        Site, null=True, blank=True, on_delete=models.CASCADE, related_name='+', help_text='Kosong = semua site.',
    )
    perusahaan = models.ForeignKey(
        Perusahaan, null=True, blank=True, on_delete=models.CASCADE, related_name='+',
        help_text='Kosong = semua perusahaan.',
    )
    bisa_ubah = models.BooleanField('Boleh tambah/ubah', default=False)

    class Meta:
        verbose_name = 'Coverage CCTV'
        verbose_name_plural = 'Coverage CCTV'
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'site', 'perusahaan'], nulls_distinct=False, name='cakupan_cctv_unik',
            ),
        ]

    def __str__(self):
        return f'{self.group}: {self.site or "semua site"} / {self.perusahaan or "semua perusahaan"}'



def minggu_label(monday):
    """'W45 2026' for the ISO week starting on `monday`."""
    return monday.strftime('W%V %G') if monday else ''


class CctvCoverage(models.Model):
    """Approved coverage polygon of one CCTV, labelled with the week its request was submitted.

    The latest row per CCTV is the approved coverage; older rows are earlier versions.
    """

    cctv = models.ForeignKey(Cctv, on_delete=models.CASCADE, related_name='coverage')
    minggu = models.DateField(help_text='Hari Senin pada minggu pengajuan.')
    geom = models.MultiPolygonField(srid=4326, null=True, blank=True)
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-minggu']
        verbose_name = 'CCTV coverage'
        verbose_name_plural = 'CCTV coverage'
        permissions = [('access_module', 'Can access CCTV Coverage module')]
        constraints = [models.UniqueConstraint(fields=['cctv', 'minggu'], name='coverage_unik_per_minggu')]

    def __str__(self):
        return f'{self.cctv_id} {self.minggu_iso}'

    @property
    def minggu_iso(self):
        return self.minggu.strftime('%G-W%V')


class PengajuanCoverage(models.Model):
    """One change request for one CCTV: IT -> Survey Mitra -> Survey BC -> Safety BC.

    IT's changes reach the CCTV when IT continues to digitisation; the boundary Survey Mitra
    draws shows on the map straight away. Safety's approval stores it as a CctvCoverage.
    The flow lives in cctv.coverage.
    """

    class Jenis(models.TextChoices):
        BARU = 'baru', 'CCTV baru'
        DIPINDAH = 'dipindah', 'Perubahan geometri'
        UBAH = 'ubah', 'Perubahan data'

    class Status(models.TextChoices):
        DRAFT_IT = 'draft_it', 'Draft IT'
        MENUNGGU_MITRA = 'menunggu_mitra', 'Waiting Survey Mitra'
        MENUNGGU_SGI = 'menunggu_sgi', 'Waiting Survey BC'
        MENUNGGU_SAFETY = 'menunggu_safety', 'Waiting Safety BC'
        RETURN_IT = 'return_it', 'Return to IT'
        RETURN_MITRA = 'return_mitra', 'Return to Survey Mitra'
        RETURN_SGI = 'return_sgi', 'Return to SGI'
        APPROVED = 'approved', 'Approved'
        APPROVED_WARNING = 'approved_warning', 'Approved with warning'

    SELESAI = (Status.APPROVED, Status.APPROVED_WARNING)
    # Which tab of the request page works on each status.
    TAHAP = {
        Status.DRAFT_IT: 'it', Status.RETURN_IT: 'it',
        Status.MENUNGGU_MITRA: 'mitra', Status.RETURN_MITRA: 'mitra',
        Status.MENUNGGU_SGI: 'sgi', Status.RETURN_SGI: 'sgi',
        Status.MENUNGGU_SAFETY: 'safety',
        Status.APPROVED: 'selesai', Status.APPROVED_WARNING: 'selesai',
    }
    BADGE = {
        Status.DRAFT_IT: 'light', Status.MENUNGGU_MITRA: 'info', Status.MENUNGGU_SGI: 'info',
        Status.MENUNGGU_SAFETY: 'info', Status.RETURN_IT: 'warning', Status.RETURN_MITRA: 'warning',
        Status.RETURN_SGI: 'warning', Status.APPROVED: 'success', Status.APPROVED_WARNING: 'warning',
    }

    CHECKLIST_SGI = (
        ('batas_objek', 'Batas sesuai objek di orthophoto'),
        ('jarak_pandang', 'Tidak melebihi jarak pandang efektif'),
        ('area_terhalang', 'Area terhalang (bench/timbunan) dikeluarkan'),
    )
    CHECKLIST_SAFETY = (
        ('kamera_online', 'Kamera online & gambar jelas'),
        ('lensa_bersih', 'Lensa bersih, tidak terhalang'),
        ('sudut_pandang', 'Sudut pandang sesuai pengajuan'),
        ('layak_awasi', 'Layak untuk pengawasan berjarak (P-SFO-04)'),
    )

    class SumberGeom(models.TextChoices):
        GAMBAR = 'gambar', 'Digambar di peta'
        SHP = 'shp', 'Upload SHP'

    # Empty only while a new CCTV is still a draft: the CCTV is created when IT continues.
    cctv = models.ForeignKey(Cctv, null=True, blank=True, on_delete=models.CASCADE, related_name='pengajuan_coverage')
    jenis = models.CharField(max_length=10, choices=Jenis.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT_IT)
    minggu = models.DateField(null=True, blank=True, help_text='Hari Senin pada minggu IT melanjutkan ke digitasi.')
    # IT's form as typed, until IT continues (then it is applied to the CCTV). Never holds a password.
    data_draft = models.JSONField(default=dict, blank=True)
    tangkapan_layar = models.FileField(upload_to='coverage/tangkapan/%Y/%m/', blank=True)
    keterangan = models.TextField('Catatan IT', blank=True)
    # The CCTV as it was before this request, for the "before" side of the reviews.
    posisi_sebelum = models.PointField(srid=4326, null=True, blank=True)
    geom_sebelum = models.MultiPolygonField(srid=4326, null=True, blank=True)
    # The geometry fields IT changed: [[label, before, after], ...]
    perubahan = models.JSONField(default=list, blank=True)
    geom = models.MultiPolygonField('Usulan coverage', srid=4326, null=True, blank=True)
    sumber_geom = models.CharField(max_length=10, choices=SumberGeom.choices, blank=True)
    file_shp = models.CharField(max_length=255, blank=True)
    versi = models.PositiveSmallIntegerField(default=1, help_text='Nomor versi coverage yang diusulkan.')
    orthophoto = models.ForeignKey(
        'orthophoto.Orthophoto', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='Orthophoto aktif saat digitasi.',
    )
    diajukan_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='pengajuan_coverage',
    )
    digitasi_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    digitasi_pada = models.DateTimeField(null=True, blank=True)
    checklist_sgi = models.JSONField(default=list, blank=True)
    checklist_safety = models.JSONField(default=list, blank=True)
    feedback_safety = models.TextField(blank=True)
    selesai = models.DateTimeField(null=True, blank=True)
    dibuat = models.DateTimeField(auto_now_add=True)
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-diubah']
        verbose_name = 'pengajuan perubahan CCTV'
        verbose_name_plural = 'pengajuan perubahan CCTV'
        indexes = [
            models.Index(fields=['status', '-diubah'], name='pengajuan_status'),
            models.Index(fields=['cctv', '-dibuat'], name='pengajuan_cctv_terbaru'),
        ]
        constraints = [
            # One open request per CCTV: a CCTV has exactly one status at a time.
            models.UniqueConstraint(
                fields=['cctv'], condition=~models.Q(status__in=['approved', 'approved_warning']),
                name='pengajuan_satu_terbuka',
            ),
        ]

    def __str__(self):
        return f'{self.no_cctv} - {self.get_status_display()}'

    @property
    def no_cctv(self):
        if self.cctv_id:
            return self.cctv.no_cctv
        typed = (self.data_draft.get('no_cctv') or [''])[0].strip()  # draft values are lists, as posted
        return typed or '(CCTV baru)'

    @property
    def tahap(self):
        return self.TAHAP[self.status]

    @property
    def terbuka(self):
        return self.status not in self.SELESAI

    @property
    def badge(self):
        return self.BADGE[self.status]

    @property
    def minggu_label(self):
        return minggu_label(self.minggu)

    @property
    def label(self):
        """'Approved W42 2026', as the CCTV list shows it."""
        return f'{self.get_status_display()} {self.minggu_label}'.strip()

    @property
    def tangkapan_url(self):
        """The screenshot URL; it changes with the file, so browsers never show a cached older one."""
        if not self.tangkapan_layar:
            return ''
        versi = hashlib.sha1(self.tangkapan_layar.name.encode()).hexdigest()[:10]
        return f"{reverse('cctv:pengajuan_tangkapan', args=[self.pk])}?v={versi}"


class RiwayatPengajuan(models.Model):
    class Aksi(models.TextChoices):
        DRAFT = 'draft', 'Menyimpan draft'
        DIAJUKAN = 'diajukan', 'Melanjutkan ke digitasi'
        OTOMATIS = 'otomatis', 'Disetujui otomatis (tanpa perubahan geometri)'
        DIGITASI = 'digitasi', 'Mengajukan digitasi ke Survey BC'
        VALIDASI = 'validasi', 'Validasi geometri lolos'
        DISETUJUI_SGI = 'setuju_sgi', 'Disetujui Survey BC'
        DISETUJUI_SAFETY = 'setuju_safety', 'Approved'
        DISETUJUI_WARNING = 'setuju_warning', 'Approved with warning'
        DIKEMBALIKAN = 'dikembalikan', 'Dikembalikan'

    pengajuan = models.ForeignKey(PengajuanCoverage, on_delete=models.CASCADE, related_name='riwayat')
    aksi = models.CharField(max_length=20, choices=Aksi.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='+')
    catatan = models.TextField(blank=True)
    waktu = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['waktu', 'pk']
        verbose_name_plural = 'riwayat pengajuan'


class SnapshotMingguan(models.Model):
    """Every CCTV as it stood when a week closed (Sunday 24:00 WITA). Rows are never changed.

    Keeps the CCTV id and number rather than a foreign key, so a deleted CCTV stays in past weeks.
    """

    minggu = models.DateField(db_index=True, help_text='Hari Senin pada minggu yang ditutup.')
    cctv_id = models.BigIntegerField()
    no_cctv = models.CharField(max_length=40)
    data = models.JSONField(help_text='Field CCTV dan spesifikasinya.')
    posisi = models.PointField(srid=4326, null=True, blank=True)
    coverage = models.MultiPolygonField(srid=4326, null=True, blank=True)
    status_pengajuan = models.CharField(max_length=20, blank=True)
    minggu_pengajuan = models.DateField(null=True, blank=True)
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-minggu', 'no_cctv']
        verbose_name = 'snapshot mingguan CCTV'
        verbose_name_plural = 'snapshot mingguan CCTV'
        constraints = [models.UniqueConstraint(fields=['minggu', 'cctv_id'], name='snapshot_unik_per_minggu')]

    def __str__(self):
        return f'{self.no_cctv} {minggu_label(self.minggu)}'
