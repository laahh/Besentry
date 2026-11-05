from django.urls import path

from . import coverage_views

# Mounted at /cctv/dashboard-coverage/: the page sits under CCTV Management, and a path below
# /dashboard/ would make the sidebar highlight Dashboard too (it matches links by prefix).
app_name = 'coverage'

urlpatterns = [
    path('', coverage_views.page, name='index'),
    path('data.json', coverage_views.data_json, name='data'),
    path('area.geojson', coverage_views.area_geojson, name='area'),
    path('cctv.geojson', coverage_views.cctv_geojson, name='cctv'),
    path('boundary.geojson', coverage_views.boundary_geojson, name='boundary'),
    path('export.xlsx', coverage_views.export_xlsx, name='export'),
    path('<str:lapisan>.geojson', coverage_views.lapisan_geojson, name='lapisan'),
]
