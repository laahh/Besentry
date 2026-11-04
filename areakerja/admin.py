from django.contrib import admin

from .models import AreaKerja, CoverageLive, MitraSite, PengajuanAreaKerja, SnapshotCoverage, SnapshotCoverageJenis


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


class SnapshotCoverageJenisInline(admin.TabularInline):
    model = SnapshotCoverageJenis
    extra = 0
    fields = ('mode', 'jenis', 'luas_ha', 'tercover_ha', 'potensi_ha', 'tidak_ha', 'jumlah_cctv')
    readonly_fields = fields
    can_delete = False


@admin.register(SnapshotCoverage)
class SnapshotCoverageAdmin(admin.ModelAdmin):
    """Read only: a locked week is never changed."""
    list_display = ('minggu', 'site_kode', 'perusahaan_nama', 'status', 'minggu_area', 'fallback', 'catatan')
    list_filter = ('status', 'minggu', 'fallback')
    search_fields = ('site_kode', 'perusahaan_nama')
    inlines = [SnapshotCoverageJenisInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(CoverageLive)
class CoverageLiveAdmin(admin.ModelAdmin):
    list_display = ('mitra_site', 'mode', 'jenis', 'luas_ha', 'tercover_ha', 'potensi_ha', 'tidak_ha', 'dihitung_pada')
    list_filter = ('mode', 'jenis', 'mitra_site__site')
    list_select_related = ('mitra_site__site', 'mitra_site__perusahaan')
    exclude = ('geom_tercover', 'geom_potensi', 'geom_tidak')

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
