from django.contrib.gis import admin

from .models import Orthophoto


@admin.register(Orthophoto)
class OrthophotoAdmin(admin.GISModelAdmin):
    list_display = ('bulan', 'status', 'progres', 'aktif', 'file_cog', 'dibuat_oleh', 'dibuat', 'selesai')
    list_filter = ('status', 'aktif')
    # Publishes are created and switched by the worker; the admin is for looking only.
    readonly_fields = [field.name for field in Orthophoto._meta.fields]

    def has_add_permission(self, request):
        return False
