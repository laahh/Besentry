from django.apps import AppConfig
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save


class AreaKerjaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'areakerja'
    verbose_name = 'Area kerja'

    def ready(self):
        from cctv.models import Cctv, CctvCoverage, PengajuanCoverage
        from notifikasi.area_kerja import notify

        from . import kalkulasi
        from .models import AreaKerja, MitraSite, PengajuanAreaKerja, RiwayatAreaKerja
        from .signals import area_kerja_berubah

        # Live coverage follows every change of a CCTV (status, position, owner), its boundary,
        # a work area, or a Mitra Site; one recompute per transaction (see kalkulasi.jadwalkan_live).
        def changed(**kwargs):
            if getattr(settings, 'COVERAGE_LIVE_OTOMATIS', True):
                kalkulasi.jadwalkan_live()

        for model in (Cctv, CctvCoverage, PengajuanCoverage, AreaKerja, PengajuanAreaKerja, MitraSite):
            post_save.connect(changed, sender=model, dispatch_uid=f'coverage-live-save-{model.__name__}')
            post_delete.connect(changed, sender=model, dispatch_uid=f'coverage-live-delete-{model.__name__}')

        # A week that waited for its request is locked as soon as the request is Approved.
        def approved(sender, pengajuan, aksi, **kwargs):
            if aksi == RiwayatAreaKerja.Aksi.DISETUJUI:
                mitra_site = pengajuan.mitra_site
                transaction.on_commit(lambda: kalkulasi.selesaikan_menunggu(mitra_site=mitra_site))

        area_kerja_berubah.connect(approved, dispatch_uid='coverage-selesaikan-menunggu')
        area_kerja_berubah.connect(notify, dispatch_uid='notifikasi-area-kerja')
