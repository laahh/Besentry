// Monthly orthophoto as a MapLibre raster layer.
//
//   import { addOrthophotoLayer, OrthophotoControl } from '.../orthophoto-layer.js';
//   const ortho = await addOrthophotoLayer(map, { apiUrl: '/api/orthophoto/current/' });
//   if (ortho) map.addControl(new OrthophotoControl(ortho), 'top-right');
//
// The API gives the current month's tile URL, bounds and zoom range; null when none is published.

const SOURCE_ID = 'orthophoto';
const LAYER_ID = 'orthophoto';
const MONTHS = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus',
  'September', 'Oktober', 'November', 'Desember'];

export function formatBulan(bulan) {
  const [year, month] = bulan.split('-');
  return `${MONTHS[Number(month) - 1]} ${year}`;
}

// Labels and roads drawn by the basemap should stay on top of the photo.
function firstSymbolLayerId(map) {
  return map.getStyle().layers.find((layer) => layer.type === 'symbol')?.id;
}

export async function addOrthophotoLayer(map, { apiUrl, beforeId = firstSymbolLayerId(map) }) {
  const response = await fetch(apiUrl, { headers: { Accept: 'application/json' } });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Orthophoto API ${response.status}`);
  const ortho = await response.json();

  map.addSource(SOURCE_ID, {
    type: 'raster',
    tiles: [ortho.tile_url],
    tileSize: ortho.tile_size,
    bounds: ortho.bounds,
    minzoom: ortho.minzoom,
    maxzoom: ortho.maxzoom, // MapLibre enlarges the deepest tiles past this
    attribution: `Orthophoto ${formatBulan(ortho.bulan)}`,
  });
  map.addLayer({ id: LAYER_ID, type: 'raster', source: SOURCE_ID }, beforeId);
  return ortho;
}

// A small on/off switch that also shows which month is on the map.
export class OrthophotoControl {
  constructor(ortho) {
    this.ortho = ortho;
  }

  onAdd(map) {
    this.container = document.createElement('div');
    this.container.className = 'maplibregl-ctrl maplibregl-ctrl-group orthophoto-ctrl';
    const label = document.createElement('label');
    const toggle = document.createElement('input');
    toggle.type = 'checkbox';
    toggle.checked = true;
    toggle.addEventListener('change', () => {
      map.setLayoutProperty(LAYER_ID, 'visibility', toggle.checked ? 'visible' : 'none');
    });
    label.append(toggle, ` Orthophoto ${formatBulan(this.ortho.bulan)}`);
    this.container.append(label);
    return this.container;
  }

  onRemove() {
    this.container.remove();
  }
}
