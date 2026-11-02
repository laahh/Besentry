from django.contrib.auth.admin import GroupAdmin
from django.contrib.auth.models import Group
from django.contrib.gis import admin

from .models import CakupanCctv, Cctv, CctvCoverage, ControlRoom


class CakupanCctvInline(admin.TabularInline):
    model = CakupanCctv
    extra = 0
    verbose_name_plural = 'Cakupan akses CCTV (site + perusahaan yang boleh dilihat role ini)'


admin.site.unregister(Group)


@admin.register(Group)
class RoleAdmin(GroupAdmin):
    inlines = [CakupanCctvInline]


@admin.register(CakupanCctv)
class CakupanCctvAdmin(admin.ModelAdmin):
    list_display = ('group', 'site', 'perusahaan', 'bisa_ubah')
    list_filter = ('site', 'bisa_ubah')
    list_select_related = ('group', 'site', 'perusahaan')
    search_fields = ('group__name', 'perusahaan__nama')


@admin.register(ControlRoom)
class ControlRoomAdmin(admin.ModelAdmin):
    list_display = ('nama', 'site')
    list_filter = ('site',)
    list_select_related = ('site',)
    search_fields = ('nama',)


@admin.register(Cctv)
class CctvAdmin(admin.GISModelAdmin):
    # Day-to-day editing happens on /cctv/database/; admin is a fallback.
    list_display = ('no_cctv', 'nama', 'site', 'perusahaan', 'status', 'kondisi')
    list_filter = ('site', 'status', 'kondisi')
    list_select_related = ('site', 'perusahaan')
    search_fields = ('no_cctv', 'nama')
    raw_id_fields = ('lokasi', 'detail_lokasi', 'control_room')


@admin.register(CctvCoverage)
class CctvCoverageAdmin(admin.GISModelAdmin):
    list_display = ('cctv', 'minggu')
    list_select_related = ('cctv',)
    raw_id_fields = ('cctv',)
    search_fields = ('cctv__no_cctv',)
