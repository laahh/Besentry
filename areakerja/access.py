"""Who sees and fills which Mitra Site's work areas. Built on the CCTV scope (Coverage CCTV per role).

    Survey Mitra   uploads for the Mitra Sites in their Coverage CCTV (not PT Berau Coal)
    Survey BC      uploads PT Berau Coal's own areas; verifies every request as SGI
    Mining, Safety verify every request (Verifikasi BC), each for its own department
    admin          sees everything and may act for any department
"""

from cctv.access import get_scope
from navigation import roles

from .models import Departemen, MitraSite

# Each department's PIC stage and role.
DEPARTEMEN_OWNER = {
    Departemen.SGI: ('sgi', roles.SURVEY_BC),
    Departemen.MINING: ('mining', roles.MINING),
    Departemen.SAFETY: ('safety', roles.SAFETY),
}


def can_open(user):
    """Menu check: anyone who can see some CCTV can see the matching work areas."""
    return get_scope(user).can_open


def visible(scope):
    return scope.filter(MitraSite.objects.aktif().select_related('site', 'perusahaan'))


def uploader(mitra_site):
    """The PIC stage and role that upload for a Mitra Site: Survey BC for PT Berau Coal's own areas."""
    return ('sgi', roles.SURVEY_BC) if mitra_site.perusahaan.is_berau_coal else ('mitra', roles.SURVEY_MITRA)


def can_upload(scope, mitra_site):
    if mitra_site.perusahaan.is_berau_coal:
        return scope.can_work('sgi', mitra_site)
    return scope.can_digitize(mitra_site)


def departemen(scope, mitra_site):
    """The Verifikasi BC departments the user approves for at this Mitra Site."""
    return [dept.value for dept in Departemen if scope.can_work(DEPARTEMEN_OWNER[dept][0], mitra_site)]
