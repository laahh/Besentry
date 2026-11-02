from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.gis.db import models
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
    # Set only from an approved registrasi; older boundaries are in the history.
    boundary = models.MultiPolygonField(srid=4326, null=True, blank=True)
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
        verbose_name = 'cakupan akses CCTV'
        verbose_name_plural = 'cakupan akses CCTV'
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'site', 'perusahaan'], nulls_distinct=False, name='cakupan_cctv_unik',
            ),
        ]

    def __str__(self):
        return f'{self.group}: {self.site or "semua site"} / {self.perusahaan or "semua perusahaan"}'


class CctvCoverage(models.Model):
    """Coverage polygon of one CCTV for one ISO week."""

    cctv = models.ForeignKey(Cctv, on_delete=models.PROTECT, related_name='coverage')
    minggu = models.DateField(help_text='Hari Senin pada minggu coverage.')
    # Empty when the coverage was withdrawn (an approved "Dicabut" request).
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
    """A request to change a CCTV's coverage, reviewed by SGI and then Safety.

    The approved polygon becomes this week's CctvCoverage (the active version).
    """

    class Jenis(models.TextChoices):
        BARU = 'baru', 'Baru'
        DIPINDAH = 'dipindah', 'Dipindah'
        DICABUT = 'dicabut', 'Dicabut'

    class Status(models.TextChoices):
        # Made automatically when IT adds or moves a CCTV: SGI still has to draw the coverage.
        MENUNGGU_DIGITASI = 'menunggu_digitasi', 'Menunggu digitasi SGI'
        MENUNGGU_SGI = 'menunggu_sgi', 'Menunggu SGI'
        MENUNGGU_SAFETY = 'menunggu_safety', 'Menunggu Safety'
        DIKEMBALIKAN = 'dikembalikan', 'Dikembalikan'
        SELESAI = 'selesai', 'Selesai'

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

    cctv = models.ForeignKey(Cctv, on_delete=models.PROTECT, related_name='pengajuan_coverage')
    jenis = models.CharField(max_length=10, choices=Jenis.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.MENUNGGU_SGI)
    geom = models.MultiPolygonField('Usulan coverage', srid=4326, null=True, blank=True)
    versi = models.PositiveSmallIntegerField(help_text='Nomor versi coverage yang diusulkan.')
    tangkapan_layar = models.FileField(upload_to='coverage/tangkapan/%Y/%m/', blank=True)
    orthophoto = models.ForeignKey(
        'orthophoto.Orthophoto', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='Orthophoto aktif saat digitasi.',
    )
    keterangan = models.TextField(blank=True)
    diajukan_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='pengajuan_coverage',
    )
    digitasi_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    digitasi_pada = models.DateTimeField(null=True, blank=True)
    checklist_sgi = models.JSONField(default=list, blank=True)
    checklist_safety = models.JSONField(default=list, blank=True)
    # Ground control points pairing screenshot pixels with map positions:
    # {"gcp": [{"px", "py", "lon", "lat"}, ...], "lebar": px, "tinggi": px} (see cctv.coverage.clean_kalibrasi)
    kalibrasi = models.JSONField(default=dict, blank=True)
    # Homography check by Safety (cctv.homography.analyse) and the screenshot with the coverage drawn on.
    analisis = models.JSONField(default=dict, blank=True)
    bukti = models.FileField(upload_to='coverage/bukti/%Y/%m/', blank=True)
    dibuat = models.DateTimeField(auto_now_add=True)
    diubah = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-diubah']
        verbose_name = 'pengajuan coverage'
        verbose_name_plural = 'pengajuan coverage'
        indexes = [models.Index(fields=['status', '-diubah'], name='pengajuan_status')]
        constraints = [
            # One open request per CCTV, so reviewers never compare against a moving target.
            models.UniqueConstraint(
                fields=['cctv'], condition=~models.Q(status='selesai'), name='pengajuan_satu_terbuka',
            ),
        ]

    def __str__(self):
        return f'{self.cctv} - {self.get_jenis_display()} ({self.get_status_display()})'

    @property
    def tahap(self):
        """The review stage now running: 'sgi', 'safety', or '' when not under review."""
        return {self.Status.MENUNGGU_SGI: 'sgi', self.Status.MENUNGGU_SAFETY: 'safety'}.get(self.status, '')


class RiwayatPengajuan(models.Model):
    class Aksi(models.TextChoices):
        DIAJUKAN = 'diajukan', 'Mengajukan perubahan'
        DIGITASI = 'digitasi', 'Mengirim digitasi'
        VALIDASI = 'validasi', 'Validasi geometri lolos'
        KALIBRASI = 'kalibrasi', 'Menyimpan kalibrasi kamera'
        DISETUJUI_SGI = 'setuju_sgi', 'Disetujui SGI'
        DISETUJUI_SAFETY = 'setuju_safety', 'Disetujui Safety'
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


class KalibrasiKamera(models.Model):
    """GCPs pairing a CCTV screenshot with the map, solved into a homography (cctv.homography).

    One active calibration per camera, reused for later requests; switched off when the CCTV
    is moved (and Safety recalibrates when the camera was turned).
    """

    cctv = models.ForeignKey(Cctv, on_delete=models.CASCADE, related_name='kalibrasi')
    gambar = models.FileField(upload_to='coverage/kalibrasi/%Y/%m/', help_text='Tangkapan layar yang dikalibrasi.')
    gcp = models.JSONField(help_text='[{"px", "py", "lon", "lat"}, ...]')
    lebar = models.PositiveIntegerField()
    tinggi = models.PositiveIntegerField()
    hasil = models.JSONField(help_text='Matriks homography, inlier/residual per GCP, RMSE (meter).')
    aktif = models.BooleanField(default=True)
    dibuat_oleh = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-dibuat']
        verbose_name = 'kalibrasi kamera'
        verbose_name_plural = 'kalibrasi kamera'
        constraints = [
            models.UniqueConstraint(fields=['cctv'], condition=models.Q(aktif=True), name='kalibrasi_satu_aktif'),
        ]

    def __str__(self):
        return f'{self.cctv_id} {self.dibuat:%Y-%m-%d} ({len(self.gcp)} GCP)'
