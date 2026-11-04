from django.urls import path

from . import digitasi_massal_views, pengajuan_views, tasklist_views, views

app_name = 'cctv'

urlpatterns = [
    path('cctv/tasklist/', tasklist_views.tasklist, name='tasklist'),
    path('cctv/tasklist/upload-shp/', digitasi_massal_views.digitasi_massal, name='digitasi_massal'),
    path('cctv/database/', views.index, name='index'),
    path('cctv/database/peta.geojson', views.geojson, name='geojson'),
    path('cctv/database/export.csv', views.export_csv, name='export'),
    path('cctv/database/detail-lokasi/', views.detail_lokasi_options, name='detail_lokasi_options'),
    path('cctv/database/<int:pk>/', views.detail, name='detail'),
    path('cctv/database/<int:pk>/hapus/', views.delete, name='delete'),
    path('cctv/database/<int:pk>/password/', views.password, name='password'),
    path('cctv/database/<int:pk>/ajukan/', pengajuan_views.ajukan, name='ajukan'),
    path('cctv/pengajuan/baru/', pengajuan_views.baru, name='pengajuan_baru'),
    path('cctv/pengajuan/<int:pk>/', pengajuan_views.page, name='pengajuan'),
    path('cctv/pengajuan/<int:pk>/it/', pengajuan_views.it, name='pengajuan_it'),
    path('cctv/pengajuan/<int:pk>/mitra/', pengajuan_views.mitra, name='pengajuan_mitra'),
    path('cctv/pengajuan/<int:pk>/mitra/cek/', pengajuan_views.mitra_cek, name='pengajuan_mitra_cek'),
    path('cctv/pengajuan/<int:pk>/sgi/', pengajuan_views.sgi, name='pengajuan_sgi'),
    path('cctv/pengajuan/<int:pk>/safety/', pengajuan_views.safety, name='pengajuan_safety'),
    path('cctv/pengajuan/<int:pk>/tangkapan/', pengajuan_views.tangkapan, name='pengajuan_tangkapan'),
]
