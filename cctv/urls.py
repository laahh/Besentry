from django.urls import path

from . import coverage_views, digitasi_views, views

app_name = 'cctv'

urlpatterns = [
    path('spasial/digitasi/', digitasi_views.index, name='digitasi_index'),
    path('spasial/digitasi/<int:pk>/simpan/', digitasi_views.simpan, name='digitasi_simpan'),
    path('cctv/coverage/', coverage_views.index, name='coverage_index'),
    path('cctv/coverage/ajukan/', coverage_views.create, name='coverage_create'),
    path('cctv/coverage/<int:pk>/revisi/', coverage_views.revise, name='coverage_revise'),
    path('cctv/coverage/<int:pk>/review/', coverage_views.review, name='coverage_review'),
    path('cctv/coverage/<int:pk>/tangkapan/', coverage_views.tangkapan, name='coverage_tangkapan'),
    path('cctv/coverage/<int:pk>/kalibrasi/', coverage_views.kalibrasi, name='coverage_kalibrasi'),
    path('cctv/coverage/<int:pk>/tangkapan/upload/', coverage_views.upload_tangkapan, name='coverage_upload'),
    path('cctv/coverage/<int:pk>/bukti/', coverage_views.bukti, name='coverage_bukti'),
    path('cctv/database/', views.index, name='index'),
    path('cctv/database/tambah/', views.create, name='create'),
    path('cctv/database/peta.geojson', views.geojson, name='geojson'),
    path('cctv/database/export.csv', views.export_csv, name='export'),
    path('cctv/database/detail-lokasi/', views.detail_lokasi_options, name='detail_lokasi_options'),
    path('cctv/database/<int:pk>/', views.detail, name='detail'),
    path('cctv/database/<int:pk>/ubah/', views.update, name='update'),
    path('cctv/database/<int:pk>/hapus/', views.delete, name='delete'),
    path('cctv/database/<int:pk>/password/', views.password, name='password'),
]
