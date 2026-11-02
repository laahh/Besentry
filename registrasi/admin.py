from django.contrib.gis import admin

from .models import Registrasi


@admin.register(Registrasi)
class RegistrasiAdmin(admin.GISModelAdmin):
    list_display = ('cctv', 'tahap', 'diajukan', 'sgi_selesai', 'safety_selesai')
    list_filter = ('tahap', 'cctv__site')
    list_select_related = ('cctv',)
    search_fields = ('cctv__no_cctv', 'cctv__nama')
    raw_id_fields = ('cctv',)
