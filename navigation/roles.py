"""Role names, mirrored from the Laravel BeSentry (spatie roles -> Django groups)."""

ADMIN_HAZARD_MOTION = 'admin-hazard-motion'
IT_PAMA = 'hazard-motion-it-pama'
IT_MTL = 'hazard-motion-it-mtl'
# Mitra (contractor) accounts only see the BeWell mitra pages.
MITRA_ONLY = 'mitra-only'

ALL_ROLES = (ADMIN_HAZARD_MOTION, IT_PAMA, IT_MTL, MITRA_ONLY)
CCTV_ADMINS = (ADMIN_HAZARD_MOTION, IT_PAMA, IT_MTL)


def user_group_names(user):
    return frozenset(user.groups.values_list('name', flat=True))


def has_any_role(user, group_names, required):
    """True when no role is required, the user is a superuser, or holds one of `required`."""
    return not required or user.is_superuser or bool(group_names & set(required))


def is_mitra_only(user, group_names):
    return MITRA_ONLY in group_names and not user.is_superuser
