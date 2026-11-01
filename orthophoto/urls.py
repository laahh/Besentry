from django.urls import path

from . import views

app_name = 'orthophoto'

urlpatterns = [
    path('orthophoto/', views.publish, name='publish'),
    path('orthophoto/status/', views.status, name='status'),
    path('api/orthophoto/current/', views.current, name='current'),
]
