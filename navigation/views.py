from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render

from . import roles
from .menu import MITRA_PAGES
from .pages import PAGES_BY_NAME


@login_required
def page(request, name):
    """Placeholder for a menu page that has not been built yet."""
    page_def = PAGES_BY_NAME.get(name)
    if page_def is None:
        raise Http404

    user = request.user
    group_names = roles.user_group_names(user)
    if not roles.has_any_role(user, group_names, page_def.groups):
        raise PermissionDenied
    if roles.is_mitra_only(user, group_names) and name not in MITRA_PAGES:
        raise PermissionDenied

    return render(request, 'navigation/placeholder.html', {'page': page_def})
