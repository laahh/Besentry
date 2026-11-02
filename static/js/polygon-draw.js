// Draw one polygon on a MapLibre map: click to add a vertex, drag a vertex to move it.
// No plugin: vertices are draggable markers, the shape is a GeoJSON layer.
//
//   const draw = new PolygonDraw(maplibregl, map, { onChange: (polygon) => input.value = ... });
//   draw.load(ring);  draw.undo();  draw.clear();
//
// Call it after the map's style has loaded.

const SOURCE_ID = 'draw';
const MIN_VERTICES = 3;

export class PolygonDraw {
  constructor(maplibregl, map, { onChange = () => {}, color = '#d97706' } = {}) {
    this.maplibregl = maplibregl;
    this.map = map;
    this.onChange = onChange;
    this.markers = [];

    map.addSource(SOURCE_ID, { type: 'geojson', data: emptyCollection() });
    map.addLayer({
      id: `${SOURCE_ID}-fill`, type: 'fill', source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': color, 'fill-opacity': 0.25 },
    });
    map.addLayer({
      id: `${SOURCE_ID}-line`, type: 'line', source: SOURCE_ID,
      paint: { 'line-color': color, 'line-width': 2 },
    });
    map.on('click', (event) => this.add(event.lngLat));
    map.getCanvas().style.cursor = 'crosshair';
  }

  add(lngLat) {
    const element = document.createElement('div');
    element.className = 'draw-vertex';
    const marker = new this.maplibregl.Marker({ element, draggable: true }).setLngLat(lngLat).addTo(this.map);
    marker.on('drag', () => this.update());
    this.markers.push(marker);
    this.update();
  }

  load(ring) {
    this.clear(false);
    // A closed GeoJSON ring repeats its first vertex at the end.
    const open = ring.length > 1 && ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1]
      ? ring.slice(0, -1) : ring;
    open.forEach((coords) => this.add({ lng: coords[0], lat: coords[1] }));
  }

  undo() {
    this.markers.pop()?.remove();
    this.update();
  }

  clear(notify = true) {
    this.markers.forEach((marker) => marker.remove());
    this.markers = [];
    if (notify) this.update();
  }

  polygon() {
    if (this.markers.length < MIN_VERTICES) return null;
    const ring = this.markers.map((marker) => marker.getLngLat().toArray());
    return { type: 'Polygon', coordinates: [[...ring, ring[0]]] };
  }

  update() {
    const polygon = this.polygon();
    const coords = this.markers.map((marker) => marker.getLngLat().toArray());
    const features = polygon
      ? [{ type: 'Feature', geometry: polygon, properties: {} }]
      : coords.length > 1 ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} }] : [];
    this.map.getSource(SOURCE_ID).setData({ type: 'FeatureCollection', features });
    this.onChange(polygon);
  }
}

function emptyCollection() {
  return { type: 'FeatureCollection', features: [] };
}
