from django.urls import path

from . import views
from .pages import PAGES

app_name = 'navigation'

# One URL per placeholder page; reverse with `navigation:page` and the page name.
urlpatterns = [
    path(page.path, views.page, {'name': page.name}, name='page')
    for page in PAGES
]
