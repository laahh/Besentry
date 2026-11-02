from django.contrib import admin

from .models import Notifikasi, PicSgi


@admin.register(PicSgi)
class PicSgiAdmin(admin.ModelAdmin):
    list_display = ('nama', 'no_wa', 'email', 'site', 'aktif')
    list_filter = ('aktif', 'site')
    search_fields = ('nama', 'no_wa', 'email')


@admin.register(Notifikasi)
class NotifikasiAdmin(admin.ModelAdmin):
    list_display = ('dibuat', 'kanal', 'tujuan', 'jenis', 'cctv', 'status')
    list_filter = ('status', 'kanal', 'jenis')
    list_select_related = ('cctv',)
    search_fields = ('tujuan', 'judul', 'cctv__no_cctv')
    readonly_fields = [field.name for field in Notifikasi._meta.fields]
