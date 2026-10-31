from django.contrib.gis import admin

from .models import Zona


@admin.register(Zona)
class ZonaAdmin(admin.GISModelAdmin):
    list_display = ('nama', 'dibuat')
    search_fields = ('nama',)
