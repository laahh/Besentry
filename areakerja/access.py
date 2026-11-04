"""Who sees and fills which Mitra Site's work areas. Built on the CCTV scope (Coverage CCTV per role).

    Survey Mitra   uploads for the Mitra Sites in their Coverage CCTV (not PT Berau Coal)
    Survey BC      uploads PT Berau Coal's own areas; verifies every request as SGI
    Mining, Safety verify every request (Verifikasi BC), each for its own department
    admin          sees everything and may act for any department
"""

from cctv.access import get_scope

from .models import Departemen, MitraSite


def can_open(user):
    """Menu check: anyone who can see some CCTV can see the matching work areas."""
    return get_scope(user).can_open


def visible(scope):
    return scope.filter(MitraSite.objects.aktif().select_related('site', 'perusahaan'))


def can_upload(scope, mitra_site):
    if mitra_site.perusahaan.is_berau_coal:
        return scope.full or scope.is_sgi
    return scope.full or (scope.is_mitra and scope.allows(mitra_site.site_id, mitra_site.perusahaan_id, edit=False))


def departemen(scope):
    """The Verifikasi BC departments the user approves for."""
    if scope.full:
        return list(Departemen.values)
    flags = ((Departemen.SGI, scope.is_sgi), (Departemen.MINING, scope.is_mining), (Departemen.SAFETY, scope.is_safety))
    return [dept.value for dept, on in flags if on]


def can_verify(scope):
    return bool(departemen(scope))
