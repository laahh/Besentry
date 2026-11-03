"""Sidebar menu, mirrored from the Laravel BeSentry `/maps` sidebar (layouts/sidebarWmsAdmin).

Add or reorder menu entries here; the template only renders what build_menu() returns.
"""

from dataclasses import dataclass

from django.urls import reverse
from django.utils.module_loading import import_string

from . import roles
from .pages import PAGES_BY_NAME


@dataclass(frozen=True)
class MenuItem:
    title: str
    icon: str = ''
    page: str = ''       # name in pages.PAGES (placeholder page)
    url_name: str = ''   # or a real Django URL name
    groups: tuple = ()
    children: tuple = ()
    check: str = ''      # dotted path to a function(user) -> bool, for access that is not a plain role
    hidden: bool = False  # kept in the menu definition but not shown (e.g. not ready yet)

    @property
    def required_groups(self):
        if self.groups or not self.page:
            return self.groups
        return PAGES_BY_NAME[self.page].groups

    def url(self):
        if self.page:
            return reverse('navigation:page', kwargs={'name': self.page})
        return reverse(self.url_name) if self.url_name else 'javascript:void(0)'


@dataclass(frozen=True)
class MenuSection:
    title: str
    items: tuple
    staff_only: bool = False


MAIN_MENU = (
    MenuSection('Umum', (
        MenuItem('Dashboard', 'home', url_name='dashboard:index', groups=(roles.ADMIN_HAZARD_MOTION,)),
    )),
    MenuSection('Kesiapan Alat', (
        MenuItem('CCTV Management', 'camera', children=(
            # Visible to admins and to any role with a CCTV site/perusahaan scope.
            MenuItem('CCTV Database', url_name='cctv:index', check='cctv.access.can_open'),
            MenuItem('CCTV PJA Dedicated', page='cctv-pja-dedicated'),
            MenuItem('Pengawas Control Room', page='cctv-control-room'),
        )),
        # Change requests (IT -> Survey Mitra -> Survey BC -> Safety) are opened from the CCTV
        # Database: its status column links to each request.
        MenuItem('Spasial', 'crosshair', groups=(roles.ADMIN_HAZARD_MOTION,), children=(
            MenuItem('WMS', page='spasial-wms', hidden=True),
            MenuItem('Area Kerja + Area CCTV', page='spasial-area-kerja', hidden=True),
            MenuItem('Boundary Monitoring', page='spasial-boundary-monitoring', hidden=True),
            MenuItem('Orthophoto', url_name='orthophoto:publish'),
        )),
    )),
    MenuSection('Pengaturan', (
        MenuItem('Role & Akses', 'user-check', url_name='pengaturan:role_list', groups=(roles.ADMIN_HAZARD_MOTION,)),
        MenuItem('Pengguna', 'users', url_name='pengaturan:user_list', groups=(roles.ADMIN_HAZARD_MOTION,)),
        MenuItem('PIC SGI', 'bell', url_name='notifikasi:pic_list', groups=(roles.ADMIN_HAZARD_MOTION,)),
    )),
    MenuSection('Sistem', (
        MenuItem('Zona', 'layers', url_name='admin:core_zona_changelist'),
        MenuItem('Admin', 'settings', url_name='admin:index'),
    ), staff_only=True),
)

MITRA_MENU = (
    MenuSection('Health / BeWell', (
        MenuItem('Mitra Kerja', 'briefcase', page='bewell-mitra'),
        MenuItem('Evaluasi PVT', 'eye', page='bewell-pvt'),
    )),
)


MITRA_PAGES = frozenset(item.page for section in MITRA_MENU for item in section.items if item.page)


def _render_item(item, user, group_names):
    if item.hidden:
        return None
    if not roles.has_any_role(user, group_names, item.required_groups):
        return None
    if item.check and not import_string(item.check)(user):
        return None
    children = [
        rendered for child in item.children
        if (rendered := _render_item(child, user, group_names))
    ]
    if item.children and not children:
        return None
    return {
        'title': item.title,
        'icon': item.icon,
        'url': 'javascript:void(0)' if children else item.url(),
        'children': children,
    }


def build_menu(user):
    """Return the sidebar sections visible to `user`, ready for the template."""
    if not user.is_authenticated:
        return []

    group_names = roles.user_group_names(user)
    sections = MITRA_MENU if roles.is_mitra_only(user, group_names) else MAIN_MENU

    menu = []
    for section in sections:
        if section.staff_only and not user.is_staff:
            continue
        items = [
            rendered for item in section.items
            if (rendered := _render_item(item, user, group_names))
        ]
        if items:
            menu.append({'title': section.title, 'items': items})
    return menu
