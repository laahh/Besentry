"""{{ user|nama_user }}: full name, else username, else "-" (also when the user is gone).

`{{ u.get_full_name|default:u.username }}` crashes when `u` is None, because Django resolves
filter arguments strictly; use this for optional users (diajukan_oleh, digitasi_oleh, ...).
"""

from django import template

register = template.Library()


@register.filter
def nama_user(user, empty='-'):
    if not user:
        return empty
    return user.get_full_name() or user.get_username()
