from django.urls import path

from . import views

app_name = 'cctv'

urlpatterns = [
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
