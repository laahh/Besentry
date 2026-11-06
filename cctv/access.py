"""Row-level access to CCTV by site and perusahaan.

Superusers and the Admin role see and manage every CCTV. Everyone else sees
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
from notifikasi.models import Pic

from .models import CakupanCctv, Cctv

ROLE_NAME_MAX = 150

FULL_ACCESS_ROLES = (roles.ADMIN,)
# Connection details and passwords, on top of the site/perusahaan scope. IT also deletes CCTV.
AKSES_ROLES = roles.IT_ROLES


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
    # Change requests: Survey Mitra digitises within their scope; Survey BC and Safety BC
    # review every site's requests, so they see every CCTV.
    is_mitra: bool = False
    is_sgi: bool = False
    is_safety: bool = False
    is_mining: bool = False  # verifies work areas with SGI and Safety BC
    # Stages held through a role work everywhere; through the master PIC, only in that PIC's place.
    role_stages: frozenset = frozenset()
    pic_places: tuple = ()  # (stage, site id or None, perusahaan id or None)

    @property
    def sees_all(self):
        return self.full or self.is_sgi or self.is_safety or self.is_mining

    @property
    def can_open(self):
        return self.sees_all or bool(self.rules)

    def _pic_of(self, stage, place):
        return any(
            pic_stage == stage and site in (None, place.site_id) and perusahaan in (None, place.perusahaan_id)
            for pic_stage, site, perusahaan in self.pic_places
        )

    def can_work(self, stage, place):
        """May act on this stage for a CCTV or Mitra Site: anyone may look, not everyone may decide."""
        return self.full or stage in self.role_stages or self._pic_of(stage, place)

    def can_digitize(self, place):
        # Survey Mitra's role stays within the Coverage CCTV of the user's roles.
        in_role = 'mitra' in self.role_stages and self.allows(place.site_id, place.perusahaan_id)
        return self.full or in_role or self._pic_of('mitra', place)

    @property
    def can_create(self):
        return self.full or any(rule.bisa_ubah for rule in self.rules)

    def can_delete(self, cctv):
        """Admins, and IT roles for the CCTV they may change."""
        return self.full or (self.can_see_akses and self.can_edit(cctv))

    def _rules(self, edit):
        return [rule for rule in self.rules if rule.bisa_ubah or not edit]

    def filter(self, queryset, edit=False, prefix=''):
        """`prefix` reaches the CCTV through a relation, e.g. 'cctv__' for change requests."""
        if self.full or (self.sees_all and not edit):
            return queryset
        rules = self._rules(edit)
        if not rules:
            return queryset.none()
        condition = Q()
        for rule in rules:
            condition |= rule.q(prefix)
        return queryset.filter(condition)

    def allows(self, site_id, perusahaan_id, edit=False):
        if self.full or (self.sees_all and not edit):
            return True
        return any(rule.matches(site_id, perusahaan_id) for rule in self._rules(edit))

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
        # Being assigned as PIC of a stage gives the access of that stage, like holding its role.
        # Connection details and deleting stay with the IT roles.
        assigned = list(Pic.objects.filter(aktif=True, karyawan__user=user)
                        .values_list('tahap', 'site_id', 'perusahaan_id'))
        tahap = {row[0] for row in assigned}
        held = {
            'mitra': roles.SURVEY_MITRA, 'sgi': roles.SURVEY_BC, 'safety': roles.SAFETY, 'mining': roles.MINING,
        }
        flags = {
            'role_stages': frozenset(stage for stage, role in held.items() if role in group_names),
            'pic_places': tuple(assigned),
            'can_see_akses': roles.has_any_role(user, group_names, AKSES_ROLES),
            'is_mitra': roles.SURVEY_MITRA in group_names or Pic.Tahap.MITRA in tahap,
            'is_sgi': roles.SURVEY_BC in group_names or Pic.Tahap.SGI in tahap,
            'is_safety': roles.SAFETY in group_names or Pic.Tahap.SAFETY in tahap,
            'is_mining': roles.MINING in group_names or Pic.Tahap.MINING in tahap,
        }
        if roles.has_any_role(user, group_names, FULL_ACCESS_ROLES):
            scope = Scope(full=True, **flags)
        else:
            rules = {
                Rule(*row) for row in CakupanCctv.objects.filter(group__user=user)
                .values_list('site_id', 'perusahaan_id', 'bisa_ubah').distinct()
            }
            rules |= {Rule(site, perusahaan, stage == Pic.Tahap.IT) for stage, site, perusahaan in assigned
                      if stage in (Pic.Tahap.IT, Pic.Tahap.MITRA)}
            scope = Scope(full=False, rules=tuple(rules), **flags)
    user._cctv_scope = scope
    return scope


def can_open(user):
    """Menu check: show CCTV Database only to users who can see some CCTV."""
    return get_scope(user).can_open


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
