from django.contrib import admin

from .models import Notifikasi, Pic


@admin.register(Pic)
class PicAdmin(admin.ModelAdmin):
    list_display = ('nama', 'tahap', 'no_wa', 'email', 'site', 'perusahaan', 'aktif')
    list_filter = ('tahap', 'aktif', 'site')
    list_select_related = ('site', 'perusahaan')
    raw_id_fields = ('karyawan',)
    search_fields = ('nama', 'no_wa', 'email')


@admin.register(Notifikasi)
class NotifikasiAdmin(admin.ModelAdmin):
    list_display = ('dibuat', 'kanal', 'tujuan', 'jenis', 'cctv', 'status')
    list_filter = ('status', 'kanal', 'jenis')
    list_select_related = ('cctv',)
    search_fields = ('tujuan', 'judul', 'cctv__no_cctv')
    readonly_fields = [field.name for field in Notifikasi._meta.fields]
