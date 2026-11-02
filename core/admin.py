from django.contrib.gis import admin

from .models import DetailLokasi, Karyawan, Lokasi, Perusahaan, Site, Zona


@admin.register(Zona)
class ZonaAdmin(admin.GISModelAdmin):
    list_display = ('nama', 'dibuat')
    search_fields = ('nama',)


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ('kode', 'nama')
    search_fields = ('kode', 'nama')


@admin.register(Perusahaan)
class PerusahaanAdmin(admin.ModelAdmin):
    list_display = ('nama', 'kode', 'sid_id')
    search_fields = ('nama', 'kode', '=sid_id')


@admin.register(Karyawan)
class KaryawanAdmin(admin.ModelAdmin):
    """Synced from SID (python manage.py sync_karyawan); edits here are overwritten."""

    list_display = ('sid', 'nama', 'perusahaan', 'jabatan_struktural', 'site', 'status', 'user', 'disinkron')
    list_filter = ('status', 'site')
    list_select_related = ('perusahaan', 'user')
    search_fields = ('=sid', 'nama', 'npk', 'email')
    raw_id_fields = ('perusahaan', 'user')
    readonly_fields = ('disinkron',)


@admin.register(Lokasi)
class LokasiAdmin(admin.ModelAdmin):
    list_display = ('id', 'nama', 'site')
    list_filter = ('site',)
    list_select_related = ('site',)
    search_fields = ('=id', 'nama')


@admin.register(DetailLokasi)
class DetailLokasiAdmin(admin.ModelAdmin):
    list_display = ('id', 'nama', 'lokasi')
    list_select_related = ('lokasi',)
    raw_id_fields = ('lokasi',)
    search_fields = ('=id', 'nama')
