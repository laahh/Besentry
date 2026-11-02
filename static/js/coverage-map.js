// Coverage polygons and the camera position on a MapLibre map.
//
//   addCoverage(maplibregl, map, { geometry, camera, color, dashed, id });
//   fitExtent(map, [minX, minY, maxX, maxY]);

export const ACTIVE_COLOR = '#24695c';
export const PROPOSED_COLOR = '#d97706';

export function addCoverage(maplibregl, map, { geometry, camera, color = ACTIVE_COLOR, dashed = false, id = 'coverage' }) {
  if (geometry) {
    map.addSource(id, { type: 'geojson', data: { type: 'Feature', geometry, properties: {} } });
    map.addLayer({ id: `${id}-fill`, type: 'fill', source: id, paint: { 'fill-color': color, 'fill-opacity': 0.25 } });
    map.addLayer({
      id: `${id}-line`, type: 'line', source: id,
      paint: { 'line-color': color, 'line-width': 2, ...(dashed ? { 'line-dasharray': [2, 2] } : {}) },
    });
  }
  if (camera) {
    new maplibregl.Marker({ color: '#2c323f', scale: 0.7 }).setLngLat(camera).addTo(map);
  }
}

export function fitExtent(map, extent) {
  if (!extent) return;
  const [minX, minY, maxX, maxY] = extent;
  if (minX === maxX && minY === maxY) {
    map.jumpTo({ center: [minX, minY], zoom: 16 });
    return;
  }
  map.fitBounds([[minX, minY], [maxX, maxY]], { padding: 32, maxZoom: 18, duration: 0 });
}
