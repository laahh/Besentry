// Draw and edit one polygon on a MapLibre map. No plugin: vertices are draggable markers, the shape
// and the edge midpoints are GeoJSON layers.
//
//   click the map        add a vertex at the end (closes against the first one)
//   click a midpoint     insert a vertex on that edge
//   drag a vertex        move it;  right-click a vertex: remove it
//
//   const draw = new PolygonDraw(maplibregl, map, { onChange: (polygon) => input.value = ... });
//   draw.load(ring);  draw.undo();  draw.clear();
//
// Call it after the map's style has loaded.

const SOURCE_ID = 'draw';
const MID_LAYER = `${SOURCE_ID}-mid`;
const MIN_VERTICES = 3;
const HISTORY = 50;
const HIT_PX = 6;

export class PolygonDraw {
  constructor(maplibregl, map, { onChange = () => {}, color = '#d97706' } = {}) {
    this.maplibregl = maplibregl;
    this.map = map;
    this.onChange = onChange;
    this.markers = [];
    this.history = [];  // earlier rings, for undo

    map.addSource(SOURCE_ID, { type: 'geojson', data: emptyCollection() });
    map.addLayer({
      id: `${SOURCE_ID}-fill`, type: 'fill', source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': color, 'fill-opacity': 0.25 },
    });
    map.addLayer({
      id: `${SOURCE_ID}-line`, type: 'line', source: SOURCE_ID,
      filter: ['!=', ['geometry-type'], 'Point'],
      paint: { 'line-color': color, 'line-width': 2 },
    });
    map.addLayer({
      id: MID_LAYER, type: 'circle', source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: { 'circle-radius': 4, 'circle-color': '#fff', 'circle-stroke-color': color, 'circle-stroke-width': 2 },
    });
    map.on('click', (event) => this.click(event));
    map.on('mouseenter', MID_LAYER, () => { map.getCanvas().style.cursor = 'copy'; });
    map.on('mouseleave', MID_LAYER, () => { map.getCanvas().style.cursor = 'crosshair'; });
    map.getCanvas().style.cursor = 'crosshair';
  }

  click(event) {
    if (event.originalEvent.target.closest?.('.draw-vertex')) return;
    const { x, y } = event.point;
    const [mid] = this.map.queryRenderedFeatures([[x - HIT_PX, y - HIT_PX], [x + HIT_PX, y + HIT_PX]], { layers: [MID_LAYER] });
    this.remember();
    if (mid) this.insert(mid.properties.after + 1, mid.geometry.coordinates);
    else this.insert(this.markers.length, event.lngLat.toArray());
    this.update();
  }

  insert(index, coords) {
    const element = document.createElement('div');
    element.className = 'draw-vertex';
    element.title = 'Geser untuk memindah, klik kanan untuk menghapus';
    const marker = new this.maplibregl.Marker({ element, draggable: true }).setLngLat(coords).addTo(this.map);
    marker.on('dragstart', () => this.remember());
    marker.on('drag', () => this.update());
    element.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      this.remember();
      this.remove(marker);
      this.update();
    });
    this.markers.splice(index, 0, marker);
  }

  remove(marker) {
    marker.remove();
    this.markers.splice(this.markers.indexOf(marker), 1);
  }

  ring() {
    return this.markers.map((marker) => marker.getLngLat().toArray());
  }

  setRing(ring) {
    this.markers.forEach((marker) => marker.remove());
    this.markers = [];
    ring.forEach((coords, index) => this.insert(index, coords));
  }

  remember() {
    this.history.push(this.ring());
    if (this.history.length > HISTORY) this.history.shift();
  }

  // undoable = false for the polygon already saved: undo should not go back to an empty map.
  load(ring, { undoable = true } = {}) {
    if (undoable) this.remember();
    // A closed GeoJSON ring repeats its first vertex at the end.
    const open = ring.length > 1 && ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1]
      ? ring.slice(0, -1) : ring;
    this.setRing(open);
    this.update();
  }

  undo() {
    if (!this.history.length) return;
    this.setRing(this.history.pop());
    this.update();
  }

  clear() {
    if (!this.markers.length) return;
    this.remember();
    this.setRing([]);
    this.update();
  }

  polygon() {
    if (this.markers.length < MIN_VERTICES) return null;
    const ring = this.ring();
    return { type: 'Polygon', coordinates: [[...ring, ring[0]]] };
  }

  bounds() {
    const ring = this.ring();
    if (!ring.length) return null;
    const xs = ring.map((c) => c[0]);
    const ys = ring.map((c) => c[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  update() {
    const polygon = this.polygon();
    const coords = this.ring();
    const features = polygon
      ? [{ type: 'Feature', geometry: polygon, properties: {} }]
      : coords.length > 1 ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} }] : [];
    // A midpoint on every edge (the closing one too, once it is a polygon) to insert a vertex there.
    const edges = polygon ? coords.length : coords.length - 1;
    for (let i = 0; i < edges; i += 1) {
      const [a, b] = [coords[i], coords[(i + 1) % coords.length]];
      features.push({
        type: 'Feature', properties: { after: i },
        geometry: { type: 'Point', coordinates: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] },
      });
    }
    this.map.getSource(SOURCE_ID).setData({ type: 'FeatureCollection', features });
    this.onChange(polygon);
  }
}

function emptyCollection() {
  return { type: 'FeatureCollection', features: [] };
}
