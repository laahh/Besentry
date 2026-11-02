// Planar homography between a CCTV image (pixels) and the ground (lon/lat).
//
// The ground is treated as flat, which holds near the camera on level benches and roads;
// on steep terrain the error grows, which is why the calibration reports its error in metres.
//
//   const cal = calibrate(gcps);           // gcps: [{px, py, lon, lat}], at least 4
//   cal.toGround(px, py)  -> {lon, lat} | null (above the horizon)
//   cal.toImage(lon, lat) -> {px, py} | null (behind the camera)
//   cal.rmse              -> metres (0 with exactly 4 points: nothing to check against)

const M_PER_DEG_LAT = 110574;
const M_PER_DEG_LON_EQUATOR = 111320;
export const MIN_GCP = 4;

// Local metres around a reference point; plenty accurate over a camera's few hundred metres.
function localFrame(lon0, lat0) {
  const kx = M_PER_DEG_LON_EQUATOR * Math.cos((lat0 * Math.PI) / 180);
  return {
    toLocal: (lon, lat) => [(lon - lon0) * kx, (lat - lat0) * M_PER_DEG_LAT],
    toLonLat: (x, y) => ({ lon: lon0 + x / kx, lat: lat0 + y / M_PER_DEG_LAT }),
  };
}

// Translate and scale points so their centroid is 0 and mean distance is sqrt(2) (Hartley).
function normaliser(points) {
  const n = points.length;
  const cx = points.reduce((s, p) => s + p[0], 0) / n;
  const cy = points.reduce((s, p) => s + p[1], 0) / n;
  const mean = points.reduce((s, p) => s + Math.hypot(p[0] - cx, p[1] - cy), 0) / n || 1;
  const k = Math.SQRT2 / mean;
  return [[k, 0, -k * cx], [0, k, -k * cy], [0, 0, 1]];
}

function multiply(a, b) {
  return a.map((row) => [0, 1, 2].map((j) => row[0] * b[0][j] + row[1] * b[1][j] + row[2] * b[2][j]));
}

export function invert(m) {
  const [[a, b, c], [d, e, f], [g, h, i]] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-12) return null;
  return [
    [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
    [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
    [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
  ];
}

function apply(m, x, y) {
  const w = m[2][0] * x + m[2][1] * y + m[2][2];
  if (Math.abs(w) < 1e-12) return null;
  return { x: (m[0][0] * x + m[0][1] * y + m[0][2]) / w, y: (m[1][0] * x + m[1][1] * y + m[1][2]) / w, w };
}

// Solve the 8x8 normal equations by Gaussian elimination with partial pivoting.
function solveLinear(a, b) {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
    if (Math.abs(m[pivot][col]) < 1e-12) return null; // degenerate: points on a line
    [m[col], m[pivot]] = [m[pivot], m[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
    }
  }
  return m.map((row, i) => row[n] / row[i]);
}

// Least-squares homography src -> dst (DLT with h33 = 1, normalised coordinates).
export function solveHomography(src, dst) {
  if (src.length < MIN_GCP || src.length !== dst.length) return null;
  const ts = normaliser(src);
  const td = normaliser(dst);
  const s = src.map(([x, y]) => apply(ts, x, y));
  const d = dst.map(([x, y]) => apply(td, x, y));

  const ata = Array.from({ length: 8 }, () => new Array(8).fill(0));
  const atb = new Array(8).fill(0);
  const add = (row, value) => {
    for (let i = 0; i < 8; i++) {
      atb[i] += row[i] * value;
      for (let j = 0; j < 8; j++) ata[i][j] += row[i] * row[j];
    }
  };
  s.forEach((p, i) => {
    const q = d[i];
    add([p.x, p.y, 1, 0, 0, 0, -q.x * p.x, -q.x * p.y], q.x);
    add([0, 0, 0, p.x, p.y, 1, -q.y * p.x, -q.y * p.y], q.y);
  });
  const h = solveLinear(ata, atb);
  if (!h) return null;
  const hn = [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]];
  const tdInv = invert(td);
  return tdInv ? multiply(multiply(tdInv, hn), ts) : null;
}

export function calibrate(gcps) {
  const done = gcps.filter((g) => [g.px, g.py, g.lon, g.lat].every(Number.isFinite));
  if (done.length < MIN_GCP) return null;
  const frame = localFrame(done[0].lon, done[0].lat);
  const ground = done.map((g) => frame.toLocal(g.lon, g.lat));
  const forward = solveHomography(done.map((g) => [g.px, g.py]), ground);
  const reverse = forward && invert(forward);
  if (!reverse) return null;

  // A ground point is in front of the camera when its image w has the same sign as the GCPs'.
  const sign = Math.sign(apply(reverse, ground[0][0], ground[0][1]).w);
  const toGround = (px, py) => {
    const p = apply(forward, px, py);
    const back = p && apply(reverse, p.x, p.y);
    return p && back && Math.sign(back.w) === sign ? frame.toLonLat(p.x, p.y) : null;
  };
  const toImage = (lon, lat) => {
    const [x, y] = frame.toLocal(lon, lat);
    const p = apply(reverse, x, y);
    return p && Math.sign(p.w) === sign ? { px: p.x, py: p.y } : null;
  };
  const residuals = done.map((g, i) => {
    const p = apply(forward, g.px, g.py);
    return p ? Math.hypot(p.x - ground[i][0], p.y - ground[i][1]) : Infinity;
  });
  const rmse = Math.sqrt(residuals.reduce((s, r) => s + r * r, 0) / residuals.length);
  return { toGround, toImage, rmse, residuals, count: done.length };
}
