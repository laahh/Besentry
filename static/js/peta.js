// Shared setup for every BeSentry map, so they all carry the same basemap and credit.
//
//   import { createMap } from '.../peta.js';
//   const map = createMap(maplibregl, { container: 'peta' });

// Orthophoto and map layers are produced by this department.
export const DEPARTMENT = 'Survey & Geospatial Information';

// Free basemap, no API key; replace with a self-hosted or paid style for production if needed.
export const BASEMAP_STYLE = 'https://tiles.openfreemap.org/styles/positron';
export const BERAU = [117.49, 2.15];

export function createMap(maplibregl, options) {
  const map = new maplibregl.Map({
    style: BASEMAP_STYLE,
    center: BERAU,
    zoom: 9,
    attributionControl: { customAttribution: `© ${DEPARTMENT}` },
    ...options,
  });
  map.addControl(new maplibregl.NavigationControl(), 'top-left');
  return map;
}
