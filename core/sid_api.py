"""Client for the HSE Automation APIs: BEATS (login) and SID (employees, companies).

Only the standard library is used. Every call raises ApiUnavailable when the API
cannot be reached or answers with something that is not the expected JSON, so
callers can tell "wrong password" apart from "API down".
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

EMPLOYEE_EXPAND = (
    'employee.functionalPosition,employee.structuralPosition,employee.department,'
    'employee.company,dedicatedSite,employee.status'
)


class ApiUnavailable(Exception):
    """The API could not be reached or returned an unusable answer."""


def _request(url, *, data=None, api_key=True):
    headers = {'Accept': 'application/json'}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    if api_key and settings.SID_API_KEY:
        headers['x-api-key'] = settings.SID_API_KEY
    request = urllib.request.Request(url, data=body, headers=headers, method='POST' if body else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=settings.SID_API_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        logger.warning('SID API %s answered HTTP %s', url.split('?')[0], exc.code)
        raise ApiUnavailable(f'HTTP {exc.code}') from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning('SID API %s unreachable: %s', url.split('?')[0], exc)
        raise ApiUnavailable(str(exc)) from exc


def beats_login(username, password):
    """The BEATS account as a dict when the credentials are valid, None when they are not.

    BEATS answers HTTP 200 either way; `success` tells them apart.
    """
    result = _request(settings.BEATS_LOGIN_URL, data={'username': username, 'password': password}, api_key=False)
    if not isinstance(result, dict) or 'success' not in result:
        raise ApiUnavailable('unexpected login response')
    return result if result['success'] and result.get('username') else None


def employee_detail(sid):
    """Detailed employee record (position, department, site, status), or None if unknown."""
    query = urllib.parse.urlencode({'expand': EMPLOYEE_EXPAND})
    return _request(f'{settings.SID_API_URL}/employeeInfo/bySid/{urllib.parse.quote(sid)}?{query}')


def _pages(path, params, page_size):
    page = 1
    while True:
        query = urllib.parse.urlencode({**params, 'page': page, 'size': page_size})
        result = _request(f'{settings.SID_API_URL}/api/ftwApi/{path}?{query}') or {}
        yield from result.get('results') or []
        if page >= (result.get('pageCount') or 0):
            return
        page += 1


def companies(page_size=1000):
    yield from _pages('getCompany', {}, page_size)


def employees(company_id, page_size=5000):
    yield from _pages('getEmployee', {'companyId': company_id}, page_size)
