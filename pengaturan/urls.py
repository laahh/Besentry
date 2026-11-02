from django.urls import path

from . import views

app_name = 'pengaturan'

urlpatterns = [
    path('pengaturan/role/', views.role_list, name='role_list'),
    path('pengaturan/role/tambah/', views.role_create, name='role_create'),
    path('pengaturan/role/buat-otomatis/', views.role_generate, name='role_generate'),
    path('pengaturan/role/<int:pk>/', views.role_update, name='role_update'),
    path('pengaturan/role/<int:pk>/hapus/', views.role_delete, name='role_delete'),
    path('pengaturan/pengguna/', views.user_list, name='user_list'),
    path('pengaturan/pengguna/<int:pk>/', views.user_roles, name='user_roles'),
]
