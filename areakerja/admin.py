from django.contrib import admin

from .models import AreaKerja, MitraSite, PengajuanAreaKerja


@admin.register(MitraSite)
class MitraSiteAdmin(admin.ModelAdmin):
    list_display = ('site', 'perusahaan', 'aktif', 'diubah')
    list_filter = ('aktif', 'site')
    list_select_related = ('site', 'perusahaan')
    list_editable = ('aktif',)
    search_fields = ('site__kode', 'perusahaan__nama', 'perusahaan__kode')
    autocomplete_fields = ('site', 'perusahaan')


class AreaKerjaInline(admin.TabularInline):
    model = AreaKerja
    extra = 0
    fields = ('jenis', 'sumber', 'file_shp', 'jumlah_feature', 'minggu_asal', 'luas_ha', 'dipotong_ha')
    readonly_fields = fields
    can_delete = False


@admin.register(PengajuanAreaKerja)
class PengajuanAreaKerjaAdmin(admin.ModelAdmin):
    list_display = ('mitra_site', 'minggu', 'status', 'diajukan_oleh', 'disetujui_pada')
    list_filter = ('status', 'minggu', 'mitra_site__site')
    list_select_related = ('mitra_site__site', 'mitra_site__perusahaan', 'diajukan_oleh')
    search_fields = ('mitra_site__site__kode', 'mitra_site__perusahaan__nama')
    readonly_fields = ('mitra_site', 'minggu', 'diajukan_oleh', 'diajukan_pada', 'disetujui_oleh', 'disetujui_pada')
    inlines = [AreaKerjaInline]
