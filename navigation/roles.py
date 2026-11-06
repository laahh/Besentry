"""Built-in roles (Django groups). A role says what someone does; where they do it is the Coverage CCTV
of their roles and the master PIC (site and perusahaan), never the role's name."""

ADMIN = 'Admin'
IT = 'IT'
# Mitra (contractor) accounts only see the BeWell mitra pages.
MITRA_ONLY = 'Mitra'
# CCTV change requests: IT submits, Survey Mitra digitises, Survey BC reviews, Safety approves.
SURVEY_MITRA = 'Survey Mitra'
SURVEY_BC = 'Survey BC'
SAFETY = 'Safety BC'
# Work areas are verified together by SGI (Survey BC), Mining and Safety BC.
MINING = 'Mining BC'

IT_ROLES = (IT,)
ALL_ROLES = (ADMIN, IT, MITRA_ONLY, SURVEY_MITRA, SURVEY_BC, SAFETY, MINING)
CCTV_ADMINS = (ADMIN, IT)
# See every CCTV without a Coverage CCTV row; see cctv.access.Scope.sees_all.
SEES_ALL_ROLES = (SURVEY_BC, SAFETY, MINING)


def user_group_names(user):
    return frozenset(user.groups.values_list('name', flat=True))


def has_any_role(user, group_names, required):
    """True when no role is required, the user is a superuser, or holds one of `required`."""
    return not required or user.is_superuser or bool(group_names & set(required))


def is_mitra_only(user, group_names):
    return MITRA_ONLY in group_names and not user.is_superuser
