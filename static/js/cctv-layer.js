// CCTV points as a MapLibre circle layer, coloured by kondisi.
//
//   addCctvLayer(maplibregl, map, { url: '/cctv/database/peta.geojson?site=1', detailUrl: '/cctv/database/0/' });

export const KONDISI_COLORS = {
  baik: '#51bb25',
  breakdown: '#dc3545',
  repair: '#f8b425',
  dismantle: '#2c323f',
  take_out: '#999999',
};

function popupContent(props, detailUrl) {
  const box = document.createElement('div');
  const link = document.createElement('a');
  link.href = detailUrl.replace('/0/', `/${props.id}/`);
  link.className = 'f-w-600';
  link.textContent = props.no;
  const name = document.createElement('div');
  name.className = 'f-12';
  name.textContent = props.nama;
  box.append(link, name);
  return box;
}

export async function addCctvLayer(maplibregl, map, { url, detailUrl }) {
  const response = await fetch(url, { credentials: 'same-origin' });
  // An expired session redirects to the login page (HTML), which is not GeoJSON.
  if (!response.ok || response.redirected) return null;
  const data = await response.json();

  map.addSource('cctv', { type: 'geojson', data });
  map.addLayer({
    id: 'cctv',
    type: 'circle',
    source: 'cctv',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 3, 15, 7],
      'circle-color': ['match', ['get', 'kondisi'], ...Object.entries(KONDISI_COLORS).flat(), '#999999'],
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 1,
    },
  });

  map.on('click', 'cctv', (event) => {
    const feature = event.features[0];
    new maplibregl.Popup({ offset: 8 })
      .setLngLat(feature.geometry.coordinates)
      .setDOMContent(popupContent(feature.properties, detailUrl))
      .addTo(map);
  });
  map.on('mouseenter', 'cctv', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'cctv', () => { map.getCanvas().style.cursor = ''; });

  if (data.features.length) {
    const bounds = new maplibregl.LngLatBounds();
    data.features.forEach((f) => bounds.extend(f.geometry.coordinates));
    map.fitBounds(bounds, { padding: 32, maxZoom: 15, duration: 0 });
  }
  return data;
}
