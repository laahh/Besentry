"""Sidebar menu, mirrored from the Laravel BeSentry `/maps` sidebar (layouts/sidebarWmsAdmin).

Add or reorder menu entries here; the template only renders what build_menu() returns.
"""

from dataclasses import dataclass

from django.urls import reverse

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
            MenuItem('CCTV Database', page='cctv-database'),
            MenuItem('CCTV PJA Dedicated', page='cctv-pja-dedicated'),
            MenuItem('CCTV Coverage', page='cctv-coverage'),
            MenuItem('Pengawas Control Room', page='cctv-control-room'),
        )),
        MenuItem('Spasial', 'crosshair', groups=(roles.ADMIN_HAZARD_MOTION,), children=(
            MenuItem('WMS', page='spasial-wms'),
            MenuItem('Area Kerja + Area CCTV', page='spasial-area-kerja'),
            MenuItem('Boundary Monitoring', page='spasial-boundary-monitoring'),
            MenuItem('Orthophoto', url_name='orthophoto:publish', groups=(roles.ADMIN_HAZARD_MOTION,)),
        )),
        MenuItem('DOP', 'file-text', page='dop'),
        MenuItem('DOP Safety GMO', 'shield', page='dop-safety'),
        MenuItem('HSECM Monitoring', 'activity', page='hsecm'),
    )),
    MenuSection('Control Room', (
        MenuItem('Dashboard Readiness', 'map', page='dashboard-readiness'),
        MenuItem('Smart Alert Maps', 'alert-triangle', page='smart-alert-maps'),
        MenuItem('Tasklist Issue', 'bell', children=(
            MenuItem('Task List Readiness', page='tasklist-readiness'),
            MenuItem('Task List Operasi', page='tasklist-operasi'),
            MenuItem('Alert Log Supervisory', page='supervisory-alert-log'),
        )),
    )),
    # In Laravel both entries below point to supervisory-alert-log.index as well.
    MenuSection('Log Alert & Intervensi', (
        MenuItem('Alert & Intervensi', 'alert-circle', page='supervisory-alert-log'),
        MenuItem('Task List', 'check-square', page='supervisory-alert-log'),
    )),
    MenuSection('On Off CCTV', (
        MenuItem('On Off CCTV', 'power', page='cctv-on-off'),
    )),
    MenuSection('History P2H', (
        MenuItem('History P2H', 'clipboard', page='history-p2h'),
    )),
    MenuSection('Health / BeWell', (
        MenuItem('Evaluasi Olahraga', 'heart', children=(
            MenuItem('Dashboard', page='bewell-ikhtisar'),
            MenuItem('Mitra Kerja', page='bewell-mitra'),
            MenuItem('Tren Aktivitas', page='bewell-aktivitas'),
        )),
    )),
    MenuSection('Besigma', (
        MenuItem('Tes Koneksi Jumphost', 'wifi', page='besigma-koneksi'),
    )),
    MenuSection('ISC', (
        MenuItem('Home ISC', 'home', page='isc-home'),
        MenuItem('Peta Boundary', 'map', page='isc-peta-boundary'),
        MenuItem('Risk Intervention', 'shield', page='isc-risk-intervention'),
        MenuItem('Rekap Post-Event', 'file-text', page='isc-post-event'),
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
    if not roles.has_any_role(user, group_names, item.required_groups):
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
