"""Role names, mirrored from the Laravel BeSentry (spatie roles -> Django groups)."""

ADMIN_HAZARD_MOTION = 'admin-hazard-motion'
IT_PAMA = 'hazard-motion-it-pama'
IT_MTL = 'hazard-motion-it-mtl'
IT_BC = 'hazard-motion-it-bc'
# Mitra (contractor) accounts only see the BeWell mitra pages.
MITRA_ONLY = 'mitra-only'
# CCTV change requests: IT submits, Survey Mitra digitises, Survey BC reviews, Safety approves.
# Not in the Laravel app yet.
SURVEY_MITRA = 'survey-mitra'
SURVEY_BC = 'survey-bc'
SAFETY = 'safety-approval'

IT_ROLES = (IT_PAMA, IT_MTL, IT_BC)
ALL_ROLES = (ADMIN_HAZARD_MOTION, *IT_ROLES, MITRA_ONLY, SURVEY_MITRA, SURVEY_BC, SAFETY)
CCTV_ADMINS = (ADMIN_HAZARD_MOTION, *IT_ROLES)


def user_group_names(user):
    return frozenset(user.groups.values_list('name', flat=True))


def has_any_role(user, group_names, required):
    """True when no role is required, the user is a superuser, or holds one of `required`."""
    return not required or user.is_superuser or bool(group_names & set(required))


def is_mitra_only(user, group_names):
    return MITRA_ONLY in group_names and not user.is_superuser
