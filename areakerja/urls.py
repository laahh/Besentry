from django.urls import path

from . import views

app_name = 'areakerja'

urlpatterns = [
    path('area-kerja/', views.index, name='index'),
    path('area-kerja/berlaku.geojson', views.peta_berlaku, name='peta_berlaku'),
    path('area-kerja/mitra/<int:mitra_pk>/buka/', views.buka, name='buka'),
    path('area-kerja/pengajuan/<int:pk>/', views.page, name='pengajuan'),
    path('area-kerja/pengajuan/<int:pk>/slot/', views.slot, name='slot'),
    path('area-kerja/pengajuan/<int:pk>/ajukan/', views.ajukan, name='ajukan'),
    path('area-kerja/pengajuan/<int:pk>/verifikasi/', views.verifikasi, name='verifikasi'),
    path('area-kerja/pengajuan/<int:pk>/cctv.geojson', views.cctv_sekitar, name='cctv_sekitar'),
    path('area-kerja/pengajuan/<int:pk>/anotasi/', views.anotasi_tambah, name='anotasi_tambah'),
    path('area-kerja/pengajuan/<int:pk>/anotasi/<int:anotasi_pk>/hapus/', views.anotasi_hapus, name='anotasi_hapus'),
    path('area-kerja/pengajuan/<int:pk>/anotasi/<int:anotasi_pk>/tandai/', views.anotasi_tandai, name='anotasi_tandai'),
]
