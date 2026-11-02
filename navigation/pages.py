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
    Page('cctv-coverage', 'cctv/coverage/', 'CCTV Coverage', 'CCTV Management',
         'cctv-data.import-coverage-form', roles.CCTV_ADMINS),
    Page('cctv-control-room', 'cctv/pengawas-control-room/', 'Pengawas Control Room', 'CCTV Management',
         'cctv-data.control-room.index'),
    Page('spasial-wms', 'spasial/wms/', 'WMS', 'Spasial', 'geofencing.index', (roles.ADMIN_HAZARD_MOTION,)),
    Page('spasial-area-kerja', 'spasial/area-kerja/', 'Area Kerja + Area CCTV', 'Spasial', 'geofencing.rules',
         (roles.ADMIN_HAZARD_MOTION,)),
    Page('spasial-boundary-monitoring', 'spasial/boundary-monitoring/', 'Boundary Monitoring', 'Spasial',
         'geofencing.monitoring', (roles.ADMIN_HAZARD_MOTION,)),
    Page('dop', 'dop/', 'DOP', 'Kesiapan Alat', 'daily-operation-plan.index'),
    Page('dop-safety', 'dop-safety-gmo/', 'DOP Safety GMO', 'Kesiapan Alat', 'dop-safety.dashboard'),
    Page('hsecm', 'hsecm-monitoring/', 'HSECM Monitoring', 'Kesiapan Alat', 'hsecm.dashboard'),
    # Control Room
    Page('dashboard-readiness', 'maps/', 'Dashboard Readiness', 'Control Room', 'maps.map'),
    Page('smart-alert-maps', 'smart-alert-maps/', 'Smart Alert Maps', 'Control Room', 'fullmaps'),
    Page('tasklist-readiness', 'tasklist/readiness/', 'Task List Readiness', 'Tasklist Issue',
         'cctv-data.intervensi-control-room.index'),
    Page('tasklist-operasi', 'tasklist/operasi/', 'Task List Operasi', 'Tasklist Issue',
         'intervensi-area-kerja.index'),
    Page('supervisory-alert-log', 'supervisory-alert-log/', 'Alert Log Supervisory', 'Log Alert & Intervensi',
         'supervisory-alert-log.index'),
    # On Off CCTV & P2H
    Page('cctv-on-off', 'cctv-on-off/', 'On Off CCTV', 'On Off CCTV', 'cctv-alerts-dashboard.index'),
    Page('history-p2h', 'history-p2h/', 'History P2H', 'History P2H', 'cctv-p2h-checklist.index'),
    # Health / BeWell
    Page('bewell-ikhtisar', 'bewell/ikhtisar/', 'Dashboard Evaluasi Olahraga', 'Health / BeWell',
         'evaluasi-well.index'),
    Page('bewell-mitra', 'bewell/mitra-kerja/', 'Mitra Kerja', 'Health / BeWell', 'evaluasi-well.mitra.index'),
    Page('bewell-aktivitas', 'bewell/tren-aktivitas/', 'Tren Aktivitas', 'Health / BeWell',
         'evaluasi-well.activities.index'),
    Page('bewell-pvt', 'bewell/evaluasi-pvt/', 'Evaluasi PVT', 'Health / BeWell', 'evaluasi-well.pvt.index'),
    # Besigma
    Page('besigma-koneksi', 'besigma/tes-koneksi/', 'Tes Koneksi Jumphost', 'Besigma',
         'besigma.connection-test'),
    # ISC
    Page('isc-home', 'isc/home/', 'Home ISC', 'ISC', 'isc.index'),
    Page('isc-peta-boundary', 'isc/peta-boundary/', 'Peta Boundary', 'ISC', 'isc.maps.index'),
    Page('isc-risk-intervention', 'isc/risk-intervention/', 'Risk Intervention', 'ISC',
         'isc.interventions.index'),
    Page('isc-post-event', 'isc/rekap-post-event/', 'Rekap Post-Event', 'ISC', 'isc.post-event.index'),
)

PAGES_BY_NAME = {page.name: page for page in PAGES}
