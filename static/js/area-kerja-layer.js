// Work areas in force this week (every Mitra Site together) under a map's other layers, and a small legend.
//
//   map.once('style.load', () => {
//     addAreaKerjaLayer(map, { url: '/area-kerja/berlaku.geojson' });  // first, so it stays underneath
//     ...the map's own layers...
//     map.addControl(new MapLegend([{ label: 'Coverage', color: '#24695c' }, ...AREA_LEGEND]), 'top-right');
//   });
//
// The colours differ on purpose from coverage (green / orange) and CCTV condition, so the areas read as background.

const SOURCE_ID = 'area-kerja';

export const AREA_WARNA = {
  pit: '#7e57c2',
  hauling: '#d81b60',
  infra_tambang: '#1e88e5',
  infra_non_tambang: '#00acc1',
};

const AREA_LABEL = {
  pit: 'Pit',
  hauling: 'Hauling',
  infra_tambang: 'Infra Tambang',
  infra_non_tambang: 'Infra Non Tambang',
};

export const AREA_LEGEND = [
  { heading: 'Area kerja week ini' },
  ...Object.entries(AREA_WARNA).map(([jenis, color]) => ({ label: AREA_LABEL[jenis], color, type: 'area' })),
];

const colorByJenis = ['match', ['get', 'jenis'], ...Object.entries(AREA_WARNA).flat(), '#888888'];

export function addAreaKerjaLayer(map, { url }) {
  // Layers are added now, empty, so whatever the page adds next is drawn on top of them.
  map.addSource(SOURCE_ID, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: `${SOURCE_ID}-fill`, type: 'fill', source: SOURCE_ID,
    paint: { 'fill-color': colorByJenis, 'fill-opacity': 0.16 },
  });
  map.addLayer({
    id: `${SOURCE_ID}-line`, type: 'line', source: SOURCE_ID,
    paint: { 'line-color': colorByJenis, 'line-width': 1.2, 'line-opacity': 0.9 },
  });
  return fetch(url, { credentials: 'same-origin' })
    .then((response) => (response.ok && !response.redirected ? response.json() : null))
    .then((data) => { if (data) map.getSource(SOURCE_ID).setData(data); return data; })
    .catch(() => null);
}

// items: [{ label, color, type: 'area' | 'fill' | 'line' | 'dashed' | 'dot' }] or { heading } for a small title.
// Place it top-right: the bottom corners hold the attribution.
export class MapLegend {
  constructor(items) {
    this.items = items;
  }

  onAdd() {
    this.container = document.createElement('div');
    this.container.className = 'maplibregl-ctrl peta-legend';
    for (const { label, color, type = 'fill', heading } of this.items) {
      if (heading) {
        const title = document.createElement('strong');
        title.textContent = heading;
        this.container.append(title);
        continue;
      }
      const row = document.createElement('div');
      const swatch = document.createElement('span');
      swatch.className = `peta-legend-${type}`;
      swatch.style.setProperty('--warna', color);
      row.append(swatch, label);
      this.container.append(row);
    }
    return this.container;
  }

  onRemove() {
    this.container.remove();
  }
}
