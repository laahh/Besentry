"""{% static_v 'css/besentry.css' %} -> /static/css/besentry.css?v=<mtime>

Browsers keep serving a cached copy of our CSS/JS after it changes (runserver sends no
cache-busting names). Adding the file's modification time to the URL makes every change a
new URL, so nobody needs a hard refresh after an update.
"""

from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def static_v(path):
    url = static(path)
    found = finders.find(path)
    return f'{url}?v={int(Path(found).stat().st_mtime)}' if found else url
