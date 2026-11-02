"""cctv.homography against a synthetic pinhole camera whose answers are known exactly."""

import math

import cv2
import numpy as np
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import SimpleTestCase

from . import homography

W, H = 1371, 775
CAMERA_LONLAT = (117.60, 2.20)
CAMERA_UTM = homography.to_utm(*CAMERA_LONLAT)


def camera_homography(height=15.0, pitch_deg=20.0, focal=1000.0):
    """Ground (x east, y north, metres from the camera foot) -> pixel, for a camera looking north."""
    t = math.radians(pitch_deg)
    x_c = np.array([1.0, 0.0, 0.0])
    z_c = np.array([0.0, math.cos(t), -math.sin(t)])  # forward, tilted down
    y_c = np.cross(z_c, x_c)                           # image "down"
    R = np.vstack([x_c, y_c, z_c])
    K = np.array([[focal, 0, W / 2], [0, focal, H / 2], [0, 0, 1]])
    return K @ np.column_stack([R[:, 0], R[:, 1], -height * R[:, 2]])


TRUE = camera_homography()


def pixel_of(x, y):
    v = TRUE @ (x, y, 1.0)
    return v[0] / v[2], v[1] / v[2]


def gcp(x, y, dx=0.0, dy=0.0):
    """A GCP for ground point (x, y); dx/dy misplace its map position (a mis-click)."""
    px, py = pixel_of(x, y)
    lon, lat = homography.to_lonlat(CAMERA_UTM[0] + x + dx, CAMERA_UTM[1] + y + dy)
    return {'px': px, 'py': py, 'lon': lon, 'lat': lat}


GROUND = [(-25, 40), (25, 40), (-40, 120), (40, 120), (0, 70), (15, 90)]


def box(x0, y0, x1, y1):
    """Ground box (metres from the camera foot) as a MultiPolygon in EPSG:4326."""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    ring = [homography.to_lonlat(CAMERA_UTM[0] + x, CAMERA_UTM[1] + y) for x, y in corners]
    return MultiPolygon(Polygon(ring, srid=4326), srid=4326)


class CalibrationTests(SimpleTestCase):
    def test_four_gcps_recover_the_camera_exactly(self):
        cal = homography.calibrate([gcp(*p) for p in GROUND[:4]], W, H)
        self.assertLess(cal.rmse_m, 0.01)
        ground = cal.to_ground(*pixel_of(10, 60))
        self.assertLess(math.dist(ground, (CAMERA_UTM[0] + 10, CAMERA_UTM[1] + 60)), 0.05)
        back = cal.to_image(CAMERA_UTM[0] + 10, CAMERA_UTM[1] + 60)
        self.assertLess(math.dist(back, pixel_of(10, 60)), 0.05)

    def test_sky_and_behind_the_camera_have_no_ground(self):
        cal = homography.calibrate([gcp(*p) for p in GROUND[:4]], W, H)
        self.assertIsNone(cal.to_ground(W / 2, 0))  # top of the frame is above the horizon here
        self.assertIsNone(cal.to_image(CAMERA_UTM[0], CAMERA_UTM[1] - 50))

    def test_ransac_flags_a_misclicked_gcp(self):
        points = [gcp(*p) for p in GROUND] + [gcp(-10, 55, dx=25)]  # 25 m off on the map
        cal = homography.calibrate(points, W, H)
        self.assertEqual(cal.inliers, [True] * 6 + [False])
        self.assertLess(cal.rmse_m, 0.05)
        self.assertGreater(cal.residuals_m[-1], 20)

    def test_degenerate_gcps_are_refused(self):
        with self.assertRaises(homography.CalibrationError):
            homography.calibrate([gcp(*p) for p in GROUND[:3]], W, H)
        on_a_line = [gcp(0, y) for y in (30, 50, 70, 90)]
        with self.assertRaises(homography.CalibrationError):
            homography.calibrate(on_a_line, W, H)

    def test_round_trip_through_json(self):
        cal = homography.calibrate([gcp(*p) for p in GROUND], W, H)
        again = homography.from_json(cal.as_json())
        self.assertEqual(cal.to_ground(700, 600), again.to_ground(700, 600))


class AnalysisTests(SimpleTestCase):
    def setUp(self):
        self.cal = homography.calibrate([gcp(*p) for p in GROUND], W, H)

    def test_coverage_in_view_is_fully_visible(self):
        result, visible, view = homography.analyse(self.cal, box(-10, 50, 10, 80), CAMERA_UTM, 300)
        self.assertGreater(result['persen_terlihat'], 99)
        self.assertEqual(result['gcp_outlier'], [])

    def test_coverage_behind_or_beyond_range_is_not_visible(self):
        behind, _, _ = homography.analyse(self.cal, box(-10, -60, 10, -20), CAMERA_UTM, 300)
        self.assertEqual(behind['persen_terlihat'], 0)
        # Half of this box lies past the 100 m range.
        half, _, _ = homography.analyse(self.cal, box(-10, 60, 10, 140), CAMERA_UTM, 100)
        self.assertTrue(45 < half['persen_terlihat'] < 55, half)
        self.assertGreater(half['titik_terjauh_m'], 100)

    def test_coverage_wider_than_the_lens_is_cut_at_the_frame_edge(self):
        # Horizontal FOV is about ±34°: at 50 m north the frame spans about ±34 m.
        wide, _, _ = homography.analyse(self.cal, box(-100, 45, 100, 55), CAMERA_UTM, 300)
        self.assertTrue(25 < wide['persen_terlihat'] < 45, wide)

    def test_evidence_image_is_drawn(self):
        blank = cv2.imencode('.png', np.full((H, W, 3), 90, np.uint8))[1].tobytes()
        result, visible, view = homography.analyse(self.cal, box(-10, 50, 10, 80), CAMERA_UTM, 300)
        jpeg = homography.render_evidence(blank, self.cal, visible, view, [gcp(*p) for p in GROUND], 'tes')
        self.assertTrue(jpeg.startswith(b'\xff\xd8'))
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        x, y = (int(v) for v in pixel_of(0, 65))
        self.assertGreater(int(image[y, x][2]) - int(image[y, x][0]), 40)  # orange-ish: red > blue
