// Fullscreen for a MapLibre map together with its overlay panels (filters, KPIs, a time slider):
// a wrapper around the map goes fullscreen, not the map alone. Needs static/css/fullscreen-panel.css.
//
//   <div class="fs-wrap" id="wrap">
//     <div class="fs-map" id="map"></div>            <!-- normal height from the page; 100vh in fullscreen -->
//     <div class="fs-only">...panel...</div>         <!-- shown only in fullscreen, over the map -->
//   </div>
//
//   import { fullscreenWithPanel } from 'besentry/fullscreen-panel';
//   const fs = fullscreenWithPanel(maplibregl, map, document.getElementById('wrap'));
//   fs.onExit(() => { /* e.g. reload the page if the panel changed the filter */ });
//
// Default (`hover: true`): hovering the map shows an "Buka peta penuh" button in its middle (always
// shown, faintly, on touch screens), and in fullscreen an exit button sits in the map's control group.
// `hover: false` uses MapLibre's own fullscreen button instead. Esc always leaves fullscreen.

const ICON_OPEN = '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>'
  + '<line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>';
const ICON_CLOSE = '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/>'
  + '<line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/>';
const svg = (paths, size) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;

// The exit button, in the map's own control group; CSS shows it only in fullscreen.
class ExitControl {
  constructor(label) { this.label = label; }

  onAdd() {
    this.container = document.createElement('div');
    this.container.className = 'maplibregl-ctrl maplibregl-ctrl-group fs-exit';
    const button = document.createElement('button');
    button.type = 'button';
    button.title = this.label;
    button.setAttribute('aria-label', this.label);
    button.innerHTML = svg(ICON_CLOSE, 18);
    button.addEventListener('click', () => { if (document.fullscreenElement) document.exitFullscreen(); });
    this.container.append(button);
    return this.container;
  }

  onRemove() { this.container.remove(); }
}

export function fullscreenWithPanel(maplibregl, map, wrapper, {
  hover = true, position = 'top-left', openLabel = 'Buka peta penuh', exitLabel = 'Keluar dari peta penuh',
} = {}) {
  if (hover) {
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'fs-open';
    open.innerHTML = `${svg(ICON_OPEN, 16)}<span>${openLabel}</span>`;
    open.addEventListener('click', () => wrapper.requestFullscreen?.());
    wrapper.append(open);
    map.addControl(new ExitControl(exitLabel), position);
  } else {
    map.addControl(new maplibregl.FullscreenControl({ container: wrapper }), position);
  }

  const enter = [];
  const exit = [];
  document.addEventListener('fullscreenchange', () => {
    map.resize();
    if (document.fullscreenElement === wrapper) enter.forEach((callback) => callback());
    else if (!document.fullscreenElement) exit.forEach((callback) => callback());
  });
  return {
    get active() { return document.fullscreenElement === wrapper; },
    open() { return wrapper.requestFullscreen?.(); },
    close() { return document.fullscreenElement ? document.exitFullscreen() : undefined; },
    onEnter(callback) { enter.push(callback); },
    onExit(callback) { exit.push(callback); },
  };
}
