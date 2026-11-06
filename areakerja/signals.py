"""Sent after every step of a work-area request; notifikasi.area_kerja sends WhatsApp and email from it.

    from areakerja.signals import area_kerja_berubah

    @receiver(area_kerja_berubah)
    def kirim(sender, pengajuan, user, aksi, **kwargs):
        # aksi: RiwayatAreaKerja.Aksi.DIAJUKAN, VERIFIKASI (one department; `departemen` is its label),
        #       DISETUJUI, DIKEMBALIKAN, DIUBAH (PT Berau Coal edited an area in force)
        ...

The Monday reminder for Mitra Sites that have not uploaded uses services.wajib_upload().
"""

from django.dispatch import Signal

area_kerja_berubah = Signal()
