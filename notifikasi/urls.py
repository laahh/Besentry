from django.urls import path

from . import views

app_name = 'notifikasi'

urlpatterns = [
    path('pengaturan/pic/', views.pic_list, name='pic_list'),
    path('pengaturan/pic/cari-karyawan/', views.cari_karyawan, name='cari_karyawan'),
    path('pengaturan/pic/<int:pk>/', views.pic_update, name='pic_update'),
    path('pengaturan/pic/<int:pk>/hapus/', views.pic_delete, name='pic_delete'),
    path('pengaturan/pic/<int:pk>/tes/', views.pic_test, name='pic_test'),
    path('pengaturan/notifikasi/<int:pk>/kirim-ulang/', views.resend, name='resend'),
]
