// Satellite imagery (Esri World Imagery) as a MapLibre raster layer, e.g. under an orthophoto so the
// land outside its extent is not a blank basemap. No API key; the attribution is shown by the map.
//
//   import { addSatelitLayer } from 'besentry/satelit-layer';
//   map.once('style.load', () => {
//     const labels = map.getStyle().layers.find((l) => l.type === 'symbol')?.id;
//     addSatelitLayer(map, { beforeId: labels });            // under the basemap's labels
//   });

export const SATELIT = {
  url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  maxzoom: 19,
  attribution: 'Citra satelit © Esri, Maxar, Earthstar Geographics',
};

export function addSatelitLayer(map, { id = 'satelit', beforeId } = {}) {
  map.addSource(id, { type: 'raster', tiles: [SATELIT.url], tileSize: 256, maxzoom: SATELIT.maxzoom, attribution: SATELIT.attribution });
  map.addLayer({ id, type: 'raster', source: id }, beforeId);
  return id;
}
