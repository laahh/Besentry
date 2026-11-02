"""Sign in with the BEATS account (HSE Automation), keeping sessions and roles in our database.

AUTHENTICATION_BACKENDS runs BeatsBackend first, then Django's ModelBackend:

- A local-only account (exists here, not an employee) skips BEATS: its password never leaves us.
- BEATS accepts the SID + password: the local account is created/updated (karyawan_sync.login_user).
- BEATS rejects it: a synced employee is refused outright (an old local password must not
  let them in); any other username falls through to ModelBackend (local admin/mitra accounts).
- BEATS is unreachable: with BEATS_OFFLINE_FALLBACK, ModelBackend checks the password hash
  saved at the last successful login.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from . import karyawan_sync, sid_api
from .models import Karyawan

logger = logging.getLogger(__name__)
UserModel = get_user_model()


class BeatsBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        username = (username or '').strip()
        if not username or not password:
            return None
        is_employee = Karyawan.objects.filter(user__username__iexact=username).exists()
        if not is_employee and UserModel.objects.filter(username__iexact=username).exists():
            # Local-only account (admin, mitra): never send its password to BEATS.
            return None

        try:
            account = sid_api.beats_login(username, password)
        except sid_api.ApiUnavailable:
            if is_employee and not settings.BEATS_OFFLINE_FALLBACK:
                raise PermissionDenied
            logger.warning('BEATS unreachable; %s falls back to the local password', username)
            return None

        if account is None:
            if is_employee:
                raise PermissionDenied  # stop ModelBackend from accepting a stale local password
            return None

        user, karyawan = karyawan_sync.login_user(account, password)
        if not karyawan.aktif and not user.is_superuser:
            user.is_active = False
            user.save(update_fields=['is_active'])
            raise PermissionDenied
        if not self.user_can_authenticate(user):
            raise PermissionDenied  # deactivated locally by an admin
        return user
