"""Who sees and fills which Mitra Site's work areas. Built on the CCTV scope (Coverage CCTV per role).

    Survey Mitra   uploads for the Mitra Sites in their Coverage CCTV (not PT Berau Coal)
    Survey BC      verifies every Mitra request; uploads PT Berau Coal's own areas
    Safety, admin  see everything
"""

from cctv.access import get_scope

from .models import MitraSite


def can_open(user):
    """Menu check: anyone who can see some CCTV can see the matching work areas."""
    return get_scope(user).can_open


def visible(scope):
    return scope.filter(MitraSite.objects.aktif().select_related('site', 'perusahaan'))


def can_upload(scope, mitra_site):
    if mitra_site.perusahaan.is_berau_coal:
        return scope.full or scope.is_sgi
    return scope.full or (scope.is_mitra and scope.allows(mitra_site.site_id, mitra_site.perusahaan_id, edit=False))


def can_verify(scope):
    return scope.can_review_sgi
