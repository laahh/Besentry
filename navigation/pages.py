"""Pages from the Laravel sidebar (layouts/sidebarWmsAdmin) that are not built yet.

Each one gets a URL and a placeholder view, so the menu works end to end.
When a page is built, point its menu item at the real view and drop it here.

The Viho sidebar marks the active link with `pathname.indexOf(href)`, so no
path may contain an earlier menu path (e.g. avoid `/maps/` and `/isc/maps/`).
"""

from dataclasses import dataclass

from . import roles


@dataclass(frozen=True)
class Page:
    name: str
    path: str
    title: str
    section: str
    legacy_route: str
    groups: tuple = ()


PAGES = (
    # Kesiapan Alat
    Page('cctv-pja-dedicated', 'cctv/pja-dedicated/', 'CCTV PJA Dedicated', 'CCTV Management',
         'cctv-data.pja-cctv-dedicated.index', roles.CCTV_ADMINS),
    Page('cctv-control-room', 'cctv/pengawas-control-room/', 'Pengawas Control Room', 'CCTV Management',
         'cctv-data.control-room.index'),
    Page('spasial-wms', 'spasial/wms/', 'WMS', 'Spasial', 'geofencing.index', (roles.ADMIN_HAZARD_MOTION,)),
    Page('spasial-area-kerja', 'spasial/area-kerja/', 'Area Kerja + Area CCTV', 'Spasial', 'geofencing.rules',
         (roles.ADMIN_HAZARD_MOTION,)),
    Page('spasial-boundary-monitoring', 'spasial/boundary-monitoring/', 'Boundary Monitoring', 'Spasial',
         'geofencing.monitoring', (roles.ADMIN_HAZARD_MOTION,)),
    # Health / BeWell (only in the mitra-only menu)
    Page('bewell-mitra', 'bewell/mitra-kerja/', 'Mitra Kerja', 'Health / BeWell', 'evaluasi-well.mitra.index'),
    Page('bewell-pvt', 'bewell/evaluasi-pvt/', 'Evaluasi PVT', 'Health / BeWell', 'evaluasi-well.pvt.index'),
)

PAGES_BY_NAME = {page.name: page for page in PAGES}
