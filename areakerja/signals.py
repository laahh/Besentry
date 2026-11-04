"""Sent after every step of a work-area request, for notifications (WhatsApp / email) to hook into.

    from areakerja.signals import area_kerja_berubah

    @receiver(area_kerja_berubah)
    def kirim(sender, pengajuan, user, aksi, **kwargs):
        # aksi: RiwayatAreaKerja.Aksi.DIAJUKAN (-> Survey BC), DISETUJUI / DIKEMBALIKAN (-> uploader),
        #       DIUBAH (PT Berau Coal edited an area in force)
        ...

The Monday reminder for Mitra Sites that have not uploaded uses services.wajib_upload().
"""

from django.dispatch import Signal

area_kerja_berubah = Signal()
