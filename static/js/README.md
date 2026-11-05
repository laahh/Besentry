# Komponen peta BeSentry

Modul ES untuk peta MapLibre GL JS (6.x). Di BeSentry dimuat lewat import map
`templates/layout/maplibre.html` dengan nama `besentry/<modul>`. Untuk project lain, salin file JS
(dan CSS-nya bila ada) lalu daftarkan di import map sendiri, atau import dengan path relatif.

| Modul | CSS | Isi |
| --- | --- | --- |
| `week-timeline.js` | `css/week-timeline.css` | Time slider week (ISO, Senin–Minggu) ala peta historis |
| `satelit-layer.js` | – | Citra satelit Esri World Imagery sebagai layer raster |
| `fullscreen-panel.js` | `css/fullscreen-panel.css` | Fullscreen peta bersama panel overlay-nya |
| `peta.js` | – | `createMap()`: basemap, center Berau, tombol zoom |
| `orthophoto-layer.js` | – | Orthophoto aktif dari API BeSentry (`/api/orthophoto/current/`) |
| `area-kerja-layer.js` | – | Area kerja per jenis dan `MapLegend` (legend dengan toggle layer) |
| `cctv-layer.js` | – | Titik CCTV per kondisi |

Tiga modul pertama tidak bergantung pada Django, Bootstrap, atau data BeSentry.

## week-timeline.js

```js
import { WeekTimeline, STATUS } from 'besentry/week-timeline';

const slider = new WeekTimeline(document.getElementById('slider'), {
  weeks: [                                   // Senin berurutan, terlama dulu
    { minggu: '2026-10-26', status: 'terkunci' },
    { minggu: '2026-11-02', status: 'live' },
  ],
  selected: '2026-11-02',
  onPick: (minggu) => muatData(minggu),      // dipanggil saat user memilih week
  statuses: { ...STATUS, menunggu: { ...STATUS.menunggu, label: 'Menunggu area kerja' } },  // opsional
});

slider.update(weeksBaru, '2026-11-02');      // data baru, mis. setelah filter berubah
slider.select('2026-10-26');                 // pindah week dari kode (tanpa onPick)
```

- Elemen container cukup `<div>` kosong; komponen membuat markup, ikon, dan kalender popup sendiri.
- Status bawaan: `live`, `terkunci`, `menunggu`, `kosong` (dua terakhir titiknya kosong). Tambah atau ubah
  lewat `statuses`: `{ label, tone: 'live' | 'netral' | 'kosong', hollow }`.
- Week aktif selalu di bawah penanda tengah. Drag (mouse/sentuh), klik, scroll, panah keyboard,
  tombol ⏮ ‹ › ⏭, dan kalender untuk berpindah; tombol menjadi abu-abu di ujung.
- Garis diisi week pudar di kiri dan kanan selebar layar; week pengisi tidak bisa dipilih.
- Warna diatur lewat custom property di container: `--wt-text`, `--wt-line`, `--wt-dot`, `--wt-accent`,
  `--wt-button`, dan lainnya (lihat `css/week-timeline.css`). Bawaannya transparan untuk di atas peta.

## satelit-layer.js

```js
import { addSatelitLayer } from 'besentry/satelit-layer';

map.once('style.load', () => {
  const labels = map.getStyle().layers.find((l) => l.type === 'symbol')?.id;
  addSatelitLayer(map, { beforeId: labels });   // di bawah label basemap; id bawaan 'satelit'
});
```

Tanpa API key; atribusi Esri otomatis tampil di peta.

## fullscreen-panel.js

```html
<div class="fs-wrap" id="wrap">
  <div class="fs-map" id="map" style="height: 600px"></div>
  <div class="fs-only panel-saya">... filter, KPI ...</div>
  <div class="fs-only slider-saya" id="slider"></div>
</div>
```

```js
import { fullscreenWithPanel } from 'besentry/fullscreen-panel';

const fs = fullscreenWithPanel(maplibregl, map, document.getElementById('wrap'));
fs.onEnter(() => { /* ... */ });
fs.onExit(() => { /* mis. muat ulang halaman bila filter berubah di panel */ });
```

Yang dibesarkan adalah `.fs-wrap`, jadi elemen `.fs-only` (hanya tampil saat fullscreen) ikut di layar penuh.
Posisi panel diatur oleh CSS halaman sendiri (`position: absolute` di dalam `.fs-wrap`).

- Bawaan (`hover: true`): saat kursor di atas peta muncul tombol **Buka peta penuh** di tengah peta (di layar
  sentuh selalu tampil samar). Saat fullscreen, tombol keluar muncul di grup kontrol peta; Esc juga keluar.
- `fullscreenWithPanel(maplibregl, map, wrap, { hover: false })` memakai tombol fullscreen MapLibre biasa.
- Teks tombol bisa diganti: `{ openLabel: 'Open full map', exitLabel: 'Exit full map' }`.
- `fs.open()` / `fs.close()` untuk membuka atau menutup dari kode, `fs.active` untuk statusnya.

Contoh lengkap pemakaian ketiganya: `templates/dashboard/coverage.html` dan `static/js/dashboard-coverage.js`.

- Survey and Geospatial Dept
