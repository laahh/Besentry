"""Model fields shared by every app."""

from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


@lru_cache
def _fernet(key):
    return Fernet(key)


class EncryptedTextField(models.TextField):
    """Text stored Fernet-encrypted with settings.FIELD_ENCRYPTION_KEY.

    The value cannot be filtered or ordered on. Losing the key makes stored values unreadable.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return _fernet(settings.FIELD_ENCRYPTION_KEY).encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        return _fernet(settings.FIELD_ENCRYPTION_KEY).decrypt(value.encode()).decode()
