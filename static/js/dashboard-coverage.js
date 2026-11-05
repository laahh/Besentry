// Dashboard Coverage: the week's coverage layers over the orthophoto, and the 12-week trend.
//
//   const peta = initCoverageMap(maplibregl, el, { lapisan: { tercover, potensi, tidak }, area, cctv, cctv_detail, ortho, fit, site },
//                                { fullscreen: wrapper });   // fullscreen takes the wrapper (map + its panels)
//   peta.reload(newUrls);   // same layers, another filter: data replaced in place, view reframed
//   peta.fullscreen.onExit(() => ...);
//
// Built from the shared map modules: peta, satelit-layer, orthophoto-layer, area-kerja-layer,
// cctv-layer and fullscreen-panel. The week time slider is static/js/week-timeline.js.
//   drawTren(el, [{ label, persen: { tercover, potensi, tidak } }, ...]);   // needs window.ApexCharts
//
// Every URL already carries the page's filter (week, mode, jenis, site, kontraktor).

import { createMap } from 'besentry/peta';
import { addOrthophotoLayer } from 'besentry/orthophoto-layer';
import { addCctvLayer, KONDISI_COLORS } from 'besentry/cctv-layer';
import { addAreaKerjaLayer, AREA_WARNA, MapLegend } from 'besentry/area-kerja-layer';
import { addSatelitLayer } from 'besentry/satelit-layer';
import { fullscreenWithPanel } from 'besentry/fullscreen-panel';

// One place for the coverage colours (layer colours are to be standardised later).
export const COVERAGE_WARNA = {
  tercover: '#1f7a3a',
  potensi: '#e8a317',
  tidak: '#d9493f',
};
const COVERAGE_LABEL = { tercover: 'Tercover', potensi: 'Potensi (CCTV mati)', tidak: 'Tidak tercover' };
const JENIS_LABEL = { pit: 'Pit', hauling: 'Hauling', infra_tambang: 'Infra Tambang', infra_non_tambang: 'Infra Non Tambang' };
const KONDISI_LABEL = { baik: 'Baik', breakdown: 'Breakdown', repair: 'Repair', dismantle: 'Dismantle', take_out: 'Take Out' };

async function getJson(url) {
  const response = await fetch(url, { credentials: 'same-origin' });
  // An expired session redirects to the login page (HTML), which is not GeoJSON.
  return response.ok && !response.redirected ? response.json() : null;
}

function popup(props) {
  const box = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'f-w-600';
  title.textContent = `${props.site} · ${props.perusahaan}`;
  const line = document.createElement('div');
  line.className = 'f-12';
  line.textContent = `${props.jenis_label} · ${COVERAGE_LABEL[props.lapisan]} ${Number(props.ha).toLocaleString('id-ID', { maximumFractionDigits: 2 })} ha`;
  box.append(title, line);
  return box;
}

function extend(bounds, geometry) {
  const walk = (coords) => (typeof coords[0] === 'number' ? bounds.extend(coords) : coords.forEach(walk));
  walk(geometry.coordinates);
}

const COVERAGE_IDS = ['tidak', 'potensi', 'tercover'];

export function initCoverageMap(maplibregl, el, urls, { fullscreen } = {}) {
  const map = createMap(maplibregl, { container: el });
  const fs = fullscreen ? fullscreenWithPanel(maplibregl, map, fullscreen)
    : (map.addControl(new maplibregl.FullscreenControl(), 'top-left'), null);
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  new ResizeObserver(() => map.resize()).observe(el);
  const peta = { map, urls, ortho: null, fullscreen: fs, reload: () => {} };

  // All sites: the orthophoto's extent, zoomed out. One site: its work areas.
  function frame(current, areaData) {
    if (current.fit === 'ortho' && peta.ortho?.bounds) {
      const [west, south, east, north] = peta.ortho.bounds;
      map.fitBounds([[west, south], [east, north]], { padding: 20, duration: 0 });
    } else if (areaData?.features.length) {
      const bounds = new maplibregl.LngLatBounds();
      areaData.features.forEach((feature) => extend(bounds, feature.geometry));
      map.fitBounds(bounds, { padding: 40, maxZoom: 16, duration: 0 });
    }
  }

  map.once('style.load', async () => {
    // Bottom to top: satellite imagery, orthophoto, work-area outlines, not covered, potential,
    // covered, CCTV points. Basemap labels stay on top of both photos.
    const labels = map.getStyle().layers.find((layer) => layer.type === 'symbol')?.id;
    addSatelitLayer(map, { beforeId: labels });
    const ortho = addOrthophotoLayer(map, { apiUrl: urls.ortho, beforeId: labels }).catch(() => null);
    const area = addAreaKerjaLayer(map, { url: urls.area });
    const lapisan = {};
    for (const name of COVERAGE_IDS) {
      const id = `coverage-${name}`;
      map.addSource(id, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id, type: 'fill', source: id, paint: { 'fill-color': COVERAGE_WARNA[name], 'fill-opacity': name === 'tercover' ? 0.45 : 0.4 } });
      map.on('click', id, (event) => {
        new maplibregl.Popup({ offset: 6 }).setLngLat(event.lngLat)
          .setDOMContent(popup({ ...event.features[0].properties, lapisan: name })).addTo(map);
      });
      map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
      lapisan[name] = getJson(urls.lapisan[name]).then((data) => { if (data) map.getSource(id).setData(data); return data; });
    }
    const cctv = addCctvLayer(maplibregl, map, { url: urls.cctv, detailUrl: urls.cctv_detail });

    const hasOrtho = await ortho;
    peta.ortho = hasOrtho;
    map.addControl(new MapLegend([
      ...(hasOrtho ? [{ label: 'Orthophoto', color: '#c9cfc9', type: 'fill', layers: ['orthophoto'] }] : []),
      { label: 'Citra satelit (Esri)', color: '#6b7a5e', type: 'fill', layers: ['satelit'] },
      { heading: 'Coverage' },
      ...['tercover', 'potensi', 'tidak'].map((name) => ({ label: COVERAGE_LABEL[name], color: COVERAGE_WARNA[name], layers: [`coverage-${name}`] })),
      { heading: 'Area kerja' },
      ...Object.entries(AREA_WARNA).map(([jenis, color]) => ({ label: JENIS_LABEL[jenis], color, type: 'line' })),
      { label: 'Outline area kerja', color: '#555555', type: 'line', layers: ['area-kerja-fill', 'area-kerja-line'] },
      { heading: 'CCTV' },
      ...Object.entries(KONDISI_LABEL).map(([key, label]) => ({ label, color: KONDISI_COLORS[key], type: 'dot' })),
      { label: 'Titik CCTV', color: '#2c323f', type: 'dot', layers: ['cctv'] },
    ]), 'top-right');

    // Last, after the CCTV layer has framed every camera (which can be far wider).
    const [areaData] = await Promise.all([area, cctv, ...Object.values(lapisan)]);
    frame(urls, areaData);

    peta.reload = async (next) => {
      const sameView = next.site === peta.urls.site;
      peta.urls = next;
      const set = (id, url) => getJson(url).then((data) => { if (data && map.getSource(id)) map.getSource(id).setData(data); return data; });
      const [nextArea] = await Promise.all([
        set('area-kerja', next.area), set('cctv', next.cctv),
        ...COVERAGE_IDS.map((name) => set(`coverage-${name}`, next.lapisan[name])),
      ]);
      if (!sameView) frame(next, nextArea);  // a new site (or back to all): reframe; same site keeps the view
    };
  });
  return peta;
}

export function drawTren(el, points) {
  if (!window.ApexCharts) return null;
  const persen = (key) => points.map((p) => (p.persen[key] === null ? null : p.persen[key]));
  const chart = new window.ApexCharts(el, {
    chart: { type: 'line', height: 260, toolbar: { show: false }, zoom: { enabled: false }, fontFamily: 'inherit' },
    series: [
      { name: 'Tercover', data: persen('tercover') },
      { name: 'Potensi', data: persen('potensi') },
    ],
    colors: [COVERAGE_WARNA.tercover, COVERAGE_WARNA.potensi],
    stroke: { width: [3, 2], curve: 'straight' },
    markers: { size: 4 },
    xaxis: { categories: points.map((p) => p.label.split(' ')[0]), labels: { rotate: -45, trim: false, style: { fontSize: '10px' } } },
    yaxis: { min: 0, max: 100, tickAmount: 5, labels: { formatter: (v) => `${Math.round(v)}%` } },
    tooltip: { y: { formatter: (v) => (v === null || v === undefined ? '-' : `${v.toLocaleString('id-ID')}%`) } },
    legend: { position: 'top', horizontalAlign: 'left' },
    grid: { borderColor: '#e6e9e6' },
    noData: { text: 'Belum ada angka' },
  });
  chart.render();
  return chart;
}
