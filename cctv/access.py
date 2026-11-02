"""Row-level access to CCTV by site and perusahaan.

Superusers and admin-hazard-motion see and manage every CCTV. Everyone else sees
the CCTV matched by the CakupanCctv rows of their groups, e.g. group
"CCTV BMO 2 - PT Pamapersada Nusantara" -> site BMO 2 + PT Pamapersada only.

    scope = get_scope(request.user)
    scope.filter(Cctv.objects.all())              # what the user may see
    scope.filter(Cctv.objects.all(), edit=True)   # what the user may change
"""

from dataclasses import dataclass

from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Q

from core.models import Perusahaan, Site
from navigation import roles

from .models import CakupanCctv, Cctv

ROLE_NAME_MAX = 150

FULL_ACCESS_ROLES = (roles.ADMIN_HAZARD_MOTION,)
# Connection details and passwords, on top of the site/perusahaan scope.
AKSES_ROLES = (roles.IT_PAMA, roles.IT_MTL)


@dataclass(frozen=True)
class Rule:
    site_id: int | None
    perusahaan_id: int | None
    bisa_ubah: bool

    def matches(self, site_id, perusahaan_id):
        return self.site_id in (None, site_id) and self.perusahaan_id in (None, perusahaan_id)

    def q(self, prefix=''):
        lookups = {}
        if self.site_id is not None:
            lookups[f'{prefix}site_id'] = self.site_id
        if self.perusahaan_id is not None:
            lookups[f'{prefix}perusahaan_id'] = self.perusahaan_id
        return Q(**lookups)


@dataclass(frozen=True)
class Scope:
    full: bool
    rules: tuple = ()
    can_see_akses: bool = False
    # Coverage workflow: SGI digitises, Safety approves. Both work on every site's requests.
    is_sgi: bool = False
    is_safety: bool = False

    @property
    def can_open(self):
        return self.full or bool(self.rules)

    @property
    def can_digitize(self):
        return self.full or self.is_sgi

    @property
    def sees_all_coverage(self):
        return self.full or self.is_sgi or self.is_safety

    def can_review(self, tahap):
        """SGI review stage (requests IT drew themselves) or the Safety stage."""
        if tahap == 'sgi':
            return self.full or self.is_sgi
        if tahap == 'safety':
            return self.full or self.is_safety
        return False

    @property
    def can_create(self):
        return self.full or any(rule.bisa_ubah for rule in self.rules)

    @property
    def can_delete(self):
        return self.full

    def _rules(self, edit):
        return [rule for rule in self.rules if rule.bisa_ubah or not edit]

    def filter(self, queryset, edit=False, prefix=''):
        """`prefix` reaches the CCTV through a relation, e.g. 'cctv__' for coverage requests."""
        if self.full:
            return queryset
        rules = self._rules(edit)
        if not rules:
            return queryset.none()
        condition = Q()
        for rule in rules:
            condition |= rule.q(prefix)
        return queryset.filter(condition)

    def allows(self, site_id, perusahaan_id, edit=False):
        return self.full or any(rule.matches(site_id, perusahaan_id) for rule in self._rules(edit))

    def can_edit(self, cctv):
        return self.allows(cctv.site_id, cctv.perusahaan_id, edit=True)

    def editable_ids(self, field):
        """Site/perusahaan ids the user may assign, or None for any."""
        rules = self._rules(edit=True)
        if self.full or any(getattr(rule, field) is None for rule in rules):
            return None
        return {getattr(rule, field) for rule in rules}


def get_scope(user):
    """The user's scope, computed once per request (cached on the user object)."""
    cached = getattr(user, '_cctv_scope', None)
    if cached is not None:
        return cached

    if not user.is_authenticated:
        scope = Scope(full=False)
    else:
        group_names = roles.user_group_names(user)
        flags = {
            'can_see_akses': roles.has_any_role(user, group_names, AKSES_ROLES),
            'is_sgi': roles.SGI in group_names,
            'is_safety': roles.SAFETY in group_names,
        }
        if roles.has_any_role(user, group_names, FULL_ACCESS_ROLES):
            scope = Scope(full=True, **flags)
        else:
            rules = tuple(
                Rule(*row) for row in CakupanCctv.objects.filter(group__user=user)
                .values_list('site_id', 'perusahaan_id', 'bisa_ubah').distinct()
            )
            scope = Scope(full=False, rules=rules, **flags)
    user._cctv_scope = scope
    return scope


def can_open(user):
    """Menu check: show CCTV Database only to users who can see some CCTV."""
    return get_scope(user).can_open


def can_open_coverage(user):
    """Menu check for Verifikasi Coverage: CCTV scope, or the SGI / Safety roles."""
    scope = get_scope(user)
    return scope.can_open or scope.sees_all_coverage


def can_digitize(user):
    """Menu check for Spasial > Digitasi Coverage."""
    return get_scope(user).can_digitize


def role_name(site, perusahaan):
    return f'CCTV {site.kode} - {perusahaan.nama}'[:ROLE_NAME_MAX]


@transaction.atomic
def create_pair_roles(bisa_ubah=False):
    """One role per site + perusahaan pair that has CCTV, e.g. "CCTV BMO 2 - PT Pamapersada Nusantara".

    Existing roles are left as they are. Returns (names of new roles, number of pairs).
    """
    pairs = list(Cctv.objects.order_by().values_list('site_id', 'perusahaan_id').distinct())
    sites, perusahaan = Site.objects.in_bulk(), Perusahaan.objects.in_bulk()
    created = []
    for site_id, perusahaan_id in pairs:
        group, is_new = Group.objects.get_or_create(name=role_name(sites[site_id], perusahaan[perusahaan_id]))
        if is_new:
            CakupanCctv.objects.create(group=group, site_id=site_id, perusahaan_id=perusahaan_id, bisa_ubah=bisa_ubah)
            created.append(group.name)
    return created, len(pairs)
