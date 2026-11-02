"""Delivery: WhatsApp through Fonnte, email through Django's SMTP settings."""

import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail


class DeliveryError(Exception):
    pass


def send_wa(target, message):
    """POST https://api.fonnte.com/send. Fonnte answers 200 with {"status": false, "reason": ...} on failure."""
    if not settings.FONNTE_TOKEN:
        raise DeliveryError('FONNTE_TOKEN belum diisi di .env.')
    body = urllib.parse.urlencode({'target': target, 'message': message, 'countryCode': '62'}).encode()
    request = urllib.request.Request(
        settings.FONNTE_URL, data=body, method='POST',
        headers={'Authorization': settings.FONNTE_TOKEN, 'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.NOTIFIKASI_TIMEOUT) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise DeliveryError(f'Fonnte tidak bisa diakses: {exc}') from exc
    if not result.get('status'):
        raise DeliveryError(f"Fonnte menolak: {result.get('reason') or result}")
    return result


def send_email(address, subject, message):
    try:
        send_mail(subject, message, None, [address], fail_silently=False)
    except Exception as exc:  # SMTP raises many kinds; all mean "not delivered"
        raise DeliveryError(f'Email gagal: {exc}') from exc
