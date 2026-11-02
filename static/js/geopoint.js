// CCTV GeoPoint: link a CCTV screenshot to the map through a calibrated homography.
//
// - Kalibrasi: pick a GCP, click it in the image, then the same spot on the map (map pins drag).
// - Titik: click the ground in the image -> pin + lon/lat on the map, or click the map -> pin in the image.
// - Overlay: the proposed (and active) coverage drawn onto the image, to see what the camera really sees.
//
// Markup: see templates/cctv/coverage/geopoint.html (element ids below).

import { calibrate, MIN_GCP } from 'besentry/homography';

const MAX_GCP = 12;
const MAX_POINTS = 12;
const LABELS = 'ABCDEFGHIJKL';
const COLORS = { usulan: '#d97706', aktif: '#24695c', gcp: '#ffd166', titik: '#4dabf7' };
const SVG_NS = 'http://www.w3.org/2000/svg';
const $ = (id) => document.getElementById(id);

export class GeoPoint {
  constructor({ maplibregl, map, image, svg, data, saveUrl, csrf, canEdit }) {
    Object.assign(this, { maplibregl, map, image, svg, data, saveUrl, csrf, canEdit });
    // The camera's saved GCPs, rescaled if this screenshot has another resolution.
    const saved = data.kalibrasi?.gcp || [];
    const sx = data.kalibrasi ? image.naturalWidth / data.kalibrasi.lebar : 1;
    const sy = data.kalibrasi ? image.naturalHeight / data.kalibrasi.tinggi : 1;
    this.gcps = saved.length
      ? saved.map((g) => ({ ...g, px: g.px * sx, py: g.py * sy }))
      : Array.from({ length: MIN_GCP }, () => ({}));
    this.selected = 0;
    this.points = [];
    this.gcpMarkers = [];
    this.pointMarkers = [];
    this.dirty = false;

    this.image.addEventListener('click', (e) => this.onImageClick(e));
    map.on('click', (e) => this.onMapClick(e.lngLat));
    this.bindControls();
    this.cal = calibrate(this.gcps);
    // Not calibrated yet and allowed to: start in calibration mode.
    this.setMode(canEdit && !saved.length ? 'kalibrasi' : 'titik');
  }

  // --- state ---------------------------------------------------------------

  get size() {
    return { w: this.image.naturalWidth, h: this.image.naturalHeight };
  }

  recalibrate() {
    this.cal = calibrate(this.gcps);
    this.points.forEach((p) => this.reproject(p));
    this.render();
  }

  reproject(p) {
    if (!this.cal) return;
    if (p.from === 'image') Object.assign(p, this.cal.toGround(p.px, p.py) || { lon: NaN, lat: NaN });
    else Object.assign(p, this.cal.toImage(p.lon, p.lat) || { px: NaN, py: NaN });
  }

  imageCoords(event) {
    const rect = this.image.getBoundingClientRect();
    return {
      px: ((event.clientX - rect.left) / rect.width) * this.size.w,
      py: ((event.clientY - rect.top) / rect.height) * this.size.h,
    };
  }

  // --- clicks --------------------------------------------------------------

  onImageClick(event) {
    const { px, py } = this.imageCoords(event);
    if (this.mode === 'kalibrasi') {
      Object.assign(this.gcps[this.selected], { px, py });
      this.changed();
      return;
    }
    if (!this.cal) return this.status('Kalibrasi kamera dulu (minimal 4 GCP) untuk mengubah klik menjadi koordinat.');
    const ground = this.cal.toGround(px, py);
    if (!ground) return this.status('Titik itu di atas cakrawala, bukan permukaan tanah.');
    this.addPoint({ from: 'image', px, py, ...ground });
  }

  onMapClick({ lng, lat }) {
    if (this.mode === 'kalibrasi') {
      Object.assign(this.gcps[this.selected], { lon: lng, lat });
      this.changed();
      return;
    }
    if (!this.cal) return this.status('Kalibrasi kamera dulu (minimal 4 GCP).');
    const pixel = this.cal.toImage(lng, lat);
    this.addPoint({ from: 'map', lon: lng, lat, ...(pixel || { px: NaN, py: NaN }) });
    if (!pixel) this.status('Titik peta itu di belakang kamera.');
  }

  addPoint(point) {
    if (this.points.length >= MAX_POINTS) this.points.shift();
    const used = new Set(this.points.map((p) => p.label));
    point.label = [...LABELS].find((l) => !used.has(l));
    this.points.push(point);
    this.render();
  }

  changed() {
    this.dirty = true;
    // Move on to the next GCP that still misses a side, so pairs go quickly.
    const g = this.gcps[this.selected];
    if (Number.isFinite(g.px) && Number.isFinite(g.lon)) {
      const next = this.gcps.findIndex((x) => !Number.isFinite(x.px) || !Number.isFinite(x.lon));
      if (next >= 0) this.selected = next;
    }
    this.recalibrate();
  }

  // --- controls ------------------------------------------------------------

  bindControls() {
    $('gp-mode-titik').addEventListener('click', () => this.setMode('titik'));
    $('gp-mode-kalibrasi')?.addEventListener('click', () => this.setMode('kalibrasi'));
    $('gp-tambah')?.addEventListener('click', () => {
      if (this.gcps.length >= MAX_GCP) return;
      this.gcps.push({});
      this.selected = this.gcps.length - 1;
      this.render();
    });
    $('gp-hapus')?.addEventListener('click', () => {
      if (this.gcps.length > MIN_GCP) this.gcps.splice(this.selected, 1);
      else this.gcps[this.selected] = {};
      this.selected = Math.min(this.selected, this.gcps.length - 1);
      this.dirty = true;
      this.recalibrate();
    });
    $('gp-simpan')?.addEventListener('click', () => this.save());
    ['gp-overlay-usulan', 'gp-overlay-aktif'].forEach((id) => $(id)?.addEventListener('change', () => this.render()));
    $('gp-salin').addEventListener('click', () => this.copy());
    $('gp-export').addEventListener('click', () => this.exportGeoJSON());
    $('gp-titik').addEventListener('click', (e) => {
      const del = e.target.closest('[data-hapus]');
      if (del) {
        this.points = this.points.filter((p) => p.label !== del.dataset.hapus);
        this.render();
      }
    });
    $('gp-chips')?.addEventListener('click', (e) => {
      const chip = e.target.closest('[data-gcp]');
      if (chip) {
        this.selected = Number(chip.dataset.gcp);
        this.setMode('kalibrasi');
      }
    });
  }

  setMode(mode) {
    this.mode = mode;
    $('gp-mode-titik').classList.toggle('active', mode === 'titik');
    $('gp-mode-kalibrasi')?.classList.toggle('active', mode === 'kalibrasi');
    $('gp-kalibrasi-panel')?.classList.toggle('d-none', mode !== 'kalibrasi');
    this.render();
  }

  async save() {
    const complete = this.gcps.filter((g) => [g.px, g.py, g.lon, g.lat].every(Number.isFinite));
    const response = await fetch(this.saveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrf },
      body: JSON.stringify({ gcp: complete, lebar: this.size.w, tinggi: this.size.h }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) return this.status(body.detail || 'Kalibrasi gagal disimpan.');
    this.dirty = false;
    this.status(`Kalibrasi tersimpan (${body.gcp} GCP, RMSE ${body.hasil?.rmse_m ?? '-'} m). Memuat hasil analisis...`);
    // The server (OpenCV) computed the analysis and the evidence image: show them.
    window.location.reload();
  }

  status(text) {
    $('gp-pesan').textContent = text;
  }

  // How far (m) the calibration puts this GCP's pixel from where it was placed on the map.
  gcpError(g) {
    const ground = this.cal.toGround(g.px, g.py);
    return ground ? metres(ground, g) : null;
  }

  // --- output --------------------------------------------------------------

  pointsGeoJSON() {
    return {
      type: 'FeatureCollection',
      features: this.points.filter((p) => Number.isFinite(p.lon)).map((p) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [round(p.lon, 7), round(p.lat, 7)] },
        properties: { titik: p.label, cctv: this.data.cctv, px: round(p.px, 1), py: round(p.py, 1) },
      })),
    };
  }

  copy() {
    const text = this.points.map((p) => `${p.label}\t${fmt(p.lat)}\t${fmt(p.lon)}`).join('\n');
    navigator.clipboard?.writeText(text).then(() => this.status('Koordinat disalin.'));
  }

  exportGeoJSON() {
    const blob = new Blob([JSON.stringify(this.pointsGeoJSON(), null, 2)], { type: 'application/geo+json' });
    const link = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(blob), download: `geopoint-${this.data.cctv}.geojson`,
    });
    link.click();
    URL.revokeObjectURL(link.href);
  }

  // --- drawing -------------------------------------------------------------

  render() {
    this.renderSvg();
    this.renderMapMarkers();
    this.renderPanel();
  }

  renderSvg() {
    const { w, h } = this.size;
    if (!w) return;
    this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    this.svg.replaceChildren();
    const scale = w / 1000; // marker sizes relative to the image width
    let partial = false;

    if (this.cal) {
      [['aktif', this.data.aktif], ['usulan', this.data.usulan]].forEach(([kind, geometry]) => {
        if (!geometry || !$(`gp-overlay-${kind}`)?.checked) return;
        rings(geometry).forEach((ring) => {
          const pixels = ring.map(([lon, lat]) => this.cal.toImage(lon, lat));
          if (pixels.some((p) => !p)) { partial = true; return; }
          this.svg.append(el('polygon', {
            points: pixels.map((p) => `${p.px},${p.py}`).join(' '),
            fill: COLORS[kind], 'fill-opacity': 0.28, stroke: COLORS[kind], 'stroke-width': 3 * scale,
            'stroke-dasharray': kind === 'aktif' ? `${10 * scale} ${6 * scale}` : 'none',
          }));
        });
      });
    }
    if (this.mode === 'kalibrasi' || $('gp-tampil-gcp')?.checked) {
      this.gcps.forEach((g, i) => {
        if (!Number.isFinite(g.px)) return;
        this.svg.append(pin(g.px, g.py, String(i + 1), COLORS.gcp, scale, i === this.selected && this.mode === 'kalibrasi'));
      });
    }
    this.points.forEach((p) => {
      if (Number.isFinite(p.px)) this.svg.append(pin(p.px, p.py, p.label, COLORS.titik, scale, false));
    });
    $('gp-sebagian')?.classList.toggle('d-none', !partial);
  }

  renderMapMarkers() {
    this.gcpMarkers.forEach((m) => m.remove());
    this.pointMarkers.forEach((m) => m.remove());
    const showGcp = this.mode === 'kalibrasi' || $('gp-tampil-gcp')?.checked;
    this.gcpMarkers = !showGcp ? [] : this.gcps.flatMap((g, i) => {
      if (!Number.isFinite(g.lon)) return [];
      const marker = new this.maplibregl.Marker({
        element: badge(String(i + 1), 'gp-pin gp-pin-gcp' + (i === this.selected ? ' aktif' : '')),
        draggable: this.mode === 'kalibrasi' && this.canEdit,
      }).setLngLat([g.lon, g.lat]).addTo(this.map);
      marker.on('dragend', () => {
        const { lng, lat } = marker.getLngLat();
        Object.assign(this.gcps[i], { lon: lng, lat });
        this.dirty = true;
        this.recalibrate();
      });
      return [marker];
    });
    this.pointMarkers = this.points.filter((p) => Number.isFinite(p.lon)).map((p) =>
      new this.maplibregl.Marker({ element: badge(p.label, 'gp-pin gp-pin-titik') })
        .setLngLat([p.lon, p.lat]).addTo(this.map));
  }

  renderPanel() {
    const chips = $('gp-chips');
    if (chips) {
      chips.innerHTML = this.gcps.map((g, i) => {
        const img = Number.isFinite(g.px), map = Number.isFinite(g.lon);
        const error = this.cal && this.cal.count > MIN_GCP && img && map ? this.gcpError(g) : null;
        const res = error === null ? '' : ` · ${error.toFixed(1)} m`;
        return `<button type="button" class="gp-chip${i === this.selected ? ' aktif' : ''}" data-gcp="${i}">`
          + `GCP ${i + 1} <span>${img ? '●' : '○'}${map ? '●' : '○'}${res}</span></button>`;
      }).join('');
    }
    const kal = this.cal
      ? `Terkalibrasi ${this.cal.count} GCP${this.cal.count > MIN_GCP ? ` · RMSE ${this.cal.rmse.toFixed(2)} m` : ' (tambah GCP ke-5 untuk mengukur akurasi)'}`
      : `Belum terkalibrasi: butuh ${MIN_GCP} GCP lengkap (gambar + peta)`;
    $('gp-kalibrasi-status').textContent = kal + (this.dirty ? ' · belum disimpan' : '');
    const simpan = $('gp-simpan');
    if (simpan) simpan.disabled = !this.cal || !this.dirty;

    $('gp-titik').innerHTML = this.points.length ? this.points.map((p) => `
      <tr>
        <td><span class="gp-label">${p.label}</span></td>
        <td>${Number.isFinite(p.px) ? `${Math.round(p.px)}, ${Math.round(p.py)}` : '<span class="text-muted">di luar gambar</span>'}</td>
        <td>${Number.isFinite(p.lat) ? `${fmt(p.lat)}, ${fmt(p.lon)}` : '<span class="text-muted">-</span>'}</td>
        <td class="text-end"><button type="button" class="btn btn-link btn-xs p-0" data-hapus="${p.label}">hapus</button></td>
      </tr>`).join('') : '<tr><td colspan="4" class="text-muted">Belum ada titik. Klik permukaan tanah di gambar CCTV atau di peta.</td></tr>';
  }
}

function rings(geometry) {
  return geometry.type === 'MultiPolygon' ? geometry.coordinates.map((p) => p[0]) : [geometry.coordinates[0]];
}

function el(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function pin(x, y, label, color, scale, active) {
  const g = el('g', {});
  const r = 13 * scale;
  if (active) g.append(el('circle', { cx: x, cy: y, r: r * 1.9, fill: 'none', stroke: color, 'stroke-width': 3 * scale }));
  g.append(el('circle', { cx: x, cy: y, r, fill: color, stroke: '#fff', 'stroke-width': 3 * scale }));
  const text = el('text', {
    x, y: y + 5 * scale, 'text-anchor': 'middle', 'font-size': 14 * scale, 'font-weight': 700, fill: '#1b1f22',
  });
  text.textContent = label;
  g.append(text);
  return g;
}

function badge(label, className) {
  const node = document.createElement('div');
  node.className = className;
  node.textContent = label;
  return node;
}

function metres(a, b) {
  const dy = (a.lat - b.lat) * 110574;
  const dx = (a.lon - b.lon) * 111320 * Math.cos((b.lat * Math.PI) / 180);
  return Math.hypot(dx, dy);
}

function round(value, digits) {
  return Number(value.toFixed(digits));
}

function fmt(value) {
  return Number.isFinite(value) ? value.toFixed(6) : '-';
}
