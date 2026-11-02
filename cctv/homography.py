"""Camera homography (OpenCV): CCTV screenshot pixels <-> ground (UTM 50N metres).

Safety pairs >= 4 ground control points (GCP) between the screenshot and the orthophoto.
From them we solve the homography with RANSAC (a mis-clicked GCP is reported as an outlier),
then answer the verification questions:

- which part of the proposed coverage the camera actually sees (the image frame projected onto
  the ground, cut at the horizon and at the camera's effective range),
- how far the coverage reaches from the camera,
- and draw the coverage onto the screenshot as evidence.

The ground is treated as a plane: accurate on benches and roads, less so across big height
differences (pits), which is why the calibration error (RMSE, metres) is always reported.
"""

import json
from dataclasses import dataclass

import cv2
import numpy as np
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Point, Polygon

UTM_50N = 32650
GCP_MIN = 4
RANSAC_THRESHOLD_M = 3.0   # a GCP further than this from the fitted plane is an outlier
EDGE_SAMPLES = 60          # points per image edge when tracing the camera's view on the ground
HORIZON_MARGIN = 1e-3      # keep this far below the horizon (in normalised w) to avoid infinity
FAR_CLAMP = 3.0            # project at most this many times the range, then cut to the range


class CalibrationError(ValueError):
    pass


def to_utm(lon, lat):
    point = Point(lon, lat, srid=4326)
    point.transform(UTM_50N)
    return point.x, point.y


def to_lonlat(x, y):
    point = Point(x, y, srid=UTM_50N)
    point.transform(4326)
    return point.x, point.y


@dataclass
class Calibration:
    """A solved camera: pixel -> ground (H) and back (H_inv), with its quality."""

    H: np.ndarray
    H_inv: np.ndarray
    sign: float            # sign of w for ground in front of the camera
    inliers: list          # per GCP: True when it agrees with the fit
    residuals_m: list      # per GCP: distance between its map spot and its projected pixel
    rmse_m: float          # over inliers
    lebar: int
    tinggi: int

    def to_ground(self, px, py):
        """Pixel -> UTM (x, y), or None above the horizon."""
        w = self.H[2] @ (px, py, 1.0)
        if w * self.sign <= 0:
            return None
        x, y, _ = (self.H @ (px, py, 1.0)) / w
        return float(x), float(y)

    def to_image(self, x, y):
        """UTM (x, y) -> pixel, or None behind the camera."""
        v = self.H_inv @ (x, y, 1.0)
        if v[2] == 0:
            return None
        px, py = v[0] / v[2], v[1] / v[2]
        return (float(px), float(py)) if self.to_ground(px, py) is not None else None

    def as_json(self):
        return {
            'H': self.H.tolist(), 'sign': self.sign, 'inliers': self.inliers, 'residuals_m': self.residuals_m,
            'rmse_m': self.rmse_m, 'lebar': self.lebar, 'tinggi': self.tinggi,
        }


def calibrate(gcps, lebar, tinggi):
    """gcps: [{'px', 'py', 'lon', 'lat'}]. Raises CalibrationError when they cannot define a camera."""
    if len(gcps) < GCP_MIN:
        raise CalibrationError(f'Butuh minimal {GCP_MIN} GCP.')
    src = np.array([[g['px'], g['py']] for g in gcps], dtype=np.float64)
    dst = np.array([to_utm(g['lon'], g['lat']) for g in gcps], dtype=np.float64)
    # Fit in metres around the first GCP: UTM's large offsets would only cost precision.
    origin = dst[0].copy()
    method = cv2.RANSAC if len(gcps) > GCP_MIN else 0
    H_local, mask = cv2.findHomography(src, dst - origin, method, RANSAC_THRESHOLD_M)
    if H_local is None or not np.isfinite(H_local).all() or abs(np.linalg.det(H_local)) < 1e-12:
        raise CalibrationError('GCP tidak membentuk kamera yang valid (titik segaris atau tertukar?).')
    shift = np.array([[1, 0, origin[0]], [0, 1, origin[1]], [0, 0, 1]], dtype=np.float64)
    H = shift @ H_local
    H_inv = np.linalg.inv(H)

    w = (H[2] @ np.c_[src, np.ones(len(src))].T)
    if not (np.all(w > 0) or np.all(w < 0)):
        raise CalibrationError('Sebagian GCP berada di atas cakrawala; pilih titik di permukaan tanah.')
    sign = float(np.sign(w[0]))
    projected = (H @ np.c_[src, np.ones(len(src))].T)
    projected = (projected[:2] / projected[2]).T
    residuals = np.linalg.norm(projected - dst, axis=1)
    inliers = (mask.ravel().astype(bool) if mask is not None else np.ones(len(gcps), bool)).tolist()
    used = residuals[np.array(inliers)] if any(inliers) else residuals
    rmse = float(np.sqrt(np.mean(used ** 2)))
    return Calibration(H, H_inv, sign, inliers, [round(float(r), 2) for r in residuals], round(rmse, 2),
                       int(lebar), int(tinggi))


def from_json(data):
    H = np.array(data['H'], dtype=np.float64)
    return Calibration(H, np.linalg.inv(H), data['sign'], data.get('inliers', []), data.get('residuals_m', []),
                       data.get('rmse_m', 0.0), data['lebar'], data['tinggi'])


def _frame_below_horizon(cal):
    """The image frame clipped to the ground side of the horizon line (Sutherland-Hodgman)."""
    w_, h_ = cal.lebar, cal.tinggi
    corners = [(0, 0), (w_, 0), (w_, h_), (0, h_)]
    points = []
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1]):
        for t in np.linspace(0, 1, EDGE_SAMPLES, endpoint=False):
            points.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    # Normalised w, so the margin does not depend on the matrix scale.
    row = cal.H[2] * cal.sign
    scale = np.abs(row).sum() or 1.0
    value = lambda p: (row @ (p[0], p[1], 1.0)) / scale - HORIZON_MARGIN  # noqa: E731
    kept = []
    for a, b in zip(points, points[1:] + points[:1]):
        va, vb = value(a), value(b)
        if va >= 0:
            kept.append(a)
        if (va >= 0) != (vb >= 0):
            t = va / (va - vb)
            kept.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return kept


def view_footprint(cal, camera_xy, jangkauan_m):
    """Ground the camera sees (UTM Polygon): frame below the horizon, cut at `jangkauan_m` from the camera."""
    pixels = _frame_below_horizon(cal)
    if len(pixels) < 3:
        return None
    cx, cy = camera_xy
    far = jangkauan_m * FAR_CLAMP
    ring = []
    for px, py in pixels:
        ground = cal.to_ground(px, py)
        if ground is None:
            continue
        dx, dy = ground[0] - cx, ground[1] - cy
        dist = (dx * dx + dy * dy) ** 0.5
        if dist > far:  # towards the horizon: pull back along the line of sight
            dx, dy = dx * far / dist, dy * far / dist
        ring.append((cx + dx, cy + dy))
    if len(ring) < 3:
        return None
    footprint = Polygon(ring + ring[:1], srid=UTM_50N).buffer(0)
    reach = Point(cx, cy, srid=UTM_50N).buffer(jangkauan_m, quadsegs=32)
    view = footprint.intersection(reach)
    return view if not view.empty else None


def analyse(cal, coverage_geom, camera_xy, jangkauan_m):
    """Verification figures for a proposed coverage (MultiPolygon in EPSG:4326)."""
    usulan = coverage_geom.transform(UTM_50N, clone=True)
    view = view_footprint(cal, camera_xy, jangkauan_m)
    visible = usulan.intersection(view) if view else None
    luas = usulan.area
    luas_terlihat = visible.area if visible and not visible.empty else 0.0
    camera = Point(*camera_xy, srid=UTM_50N)
    farthest = max(Point(x, y, srid=UTM_50N).distance(camera) for x, y in usulan.convex_hull.coords[0])
    return {
        'persen_terlihat': round(100 * luas_terlihat / luas, 1) if luas else 0.0,
        'luas_ha': round(luas / 10_000, 3),
        'luas_terlihat_ha': round(luas_terlihat / 10_000, 3),
        'luas_pandang_ha': round(view.area / 10_000, 3) if view else 0.0,
        'titik_terjauh_m': round(farthest, 1),
        'jangkauan_m': round(jangkauan_m, 1),
        'rmse_m': cal.rmse_m,
        'gcp_outlier': [i + 1 for i, ok in enumerate(cal.inliers) if not ok],
    }, visible, view


def _pixel_rings(cal, geom_utm):
    """Image-space rings of a UTM (Multi)Polygon that lies in front of the camera."""
    if geom_utm is None or geom_utm.empty:
        return []
    polygons = geom_utm if isinstance(geom_utm, MultiPolygon) else (
        [geom_utm] if isinstance(geom_utm, Polygon) else [g for g in geom_utm if isinstance(g, Polygon)])
    rings = []
    for polygon in polygons:
        pixels = [cal.to_image(x, y) for x, y in polygon.exterior_ring.coords]
        if all(pixels):
            rings.append(np.array(pixels, dtype=np.int32))
    return rings


def render_evidence(image_bytes, cal, visible_utm, view_utm, gcps, summary):
    """The screenshot with the visible coverage, the camera's view edge and the GCPs drawn on. JPEG bytes."""
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise CalibrationError('Tangkapan layar tidak bisa dibaca.')
    sx, sy = image.shape[1] / cal.lebar, image.shape[0] / cal.tinggi
    scale = lambda ring: (ring * (sx, sy)).astype(np.int32)  # noqa: E731
    thick = max(2, image.shape[1] // 500)

    overlay = image.copy()
    for ring in _pixel_rings(cal, visible_utm):
        cv2.fillPoly(overlay, [scale(ring)], (6, 119, 217))  # BGR orange
    image = cv2.addWeighted(overlay, 0.35, image, 0.65, 0)
    for ring in _pixel_rings(cal, visible_utm):
        cv2.polylines(image, [scale(ring)], True, (6, 119, 217), thick, cv2.LINE_AA)

    for i, g in enumerate(gcps):
        ok = i >= len(cal.inliers) or cal.inliers[i]
        centre = (int(g['px'] * sx), int(g['py'] * sy))
        cv2.circle(image, centre, 6 * thick, (102, 209, 255) if ok else (53, 53, 220), -1, cv2.LINE_AA)
        cv2.circle(image, centre, 6 * thick, (255, 255, 255), thick, cv2.LINE_AA)
        cv2.putText(image, str(i + 1), (centre[0] - 3 * thick, centre[1] + 3 * thick), cv2.FONT_HERSHEY_SIMPLEX,
                    0.35 * thick, (34, 31, 27), thick, cv2.LINE_AA)

    bar = int(28 * thick / 2) + 10
    cv2.rectangle(image, (0, image.shape[0] - bar), (image.shape[1], image.shape[0]), (34, 31, 27), -1)
    cv2.putText(image, summary, (10, image.shape[0] - bar // 3), cv2.FONT_HERSHEY_SIMPLEX, 0.3 * thick + 0.2,
                (255, 255, 255), max(1, thick // 2), cv2.LINE_AA)
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise CalibrationError('Gagal membuat gambar bukti.')
    return encoded.tobytes()


def geometry_to_geojson(geom_utm):
    """UTM geometry -> GeoJSON dict in EPSG:4326 (for the maps)."""
    if geom_utm is None or geom_utm.empty:
        return None
    clone = GEOSGeometry(geom_utm.wkt, srid=UTM_50N)
    clone.transform(4326)
    return json.loads(clone.geojson)
