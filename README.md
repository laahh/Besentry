# BeSentry

Django 5.2 + GeoDjango + PostGIS + Channels (realtime, via Redis).

## Stack

| Komponen | Versi | Jalan di |
|---|---|---|
| Python | 3.12 | `.venv` |
| Django | 5.2 LTS | `.venv` |
| PostgreSQL + PostGIS | 17 + 3.5 | Docker (`besentry_db`, port **5433**, karena 5432 dipakai PostgreSQL lokal) |
| Redis | 7 | Docker (`besentry_redis`, port 6379): channel layer + antrean job (django-rq) |
| GDAL / GEOS / PROJ | 3.13 | wheel di `.venv` atau OSGeo4W |
| TiTiler | 2.4.0 | Docker (`besentry_titiler`, port 8081): tile orthophoto |
| Worker | - | Docker (`besentry_worker`): konversi orthophoto |

## Install GDAL (sekali saja)

Pilih salah satu. `config/gis_windows.py` otomatis mendeteksi keduanya.

**A. Wheel ke venv (disarankan, tanpa admin)**

```powershell
.\.venv\Scripts\python.exe -m pip install https://github.com/cgohlke/geospatial-wheels/releases/download/v2026.8.20/gdal-3.13.3-cp312-cp312-win_amd64.whl
```

**B. OSGeo4W**

Download https://download.osgeo.org/osgeo4w/v2/osgeo4w-setup.exe, pilih Express Install, centang GDAL.

## Setup pertama kali

```powershell
docker compose up -d
.\.venv\Scripts\Activate.ps1
python manage.py migrate
python manage.py createsuperuser
```

## Menjalankan

```powershell
docker compose up -d
.\.venv\Scripts\Activate.ps1
python manage.py runserver          # http://127.0.0.1:8000/admin
```

Buka http://127.0.0.1:8000 (diarahkan ke `/login/`, lalu `/dashboard/`).

## Orthophoto bulanan

Dikelola oleh **Survey & Geospatial Information**. Setiap peta di BeSentry mencantumkan kredit ini (lewat `createMap` di `static/js/peta.js`).

Orthophoto terbaru disajikan sebagai tile oleh TiTiler. Hanya satu orthophoto yang disimpan; publish bulan baru menggantikan yang lama.

1. Salin file mentah (GeoTIFF RGBA, band 4 = alpha) ke `data/incoming/`.
2. Buka **Spasial > Orthophoto** (`/orthophoto/`, khusus `admin-hazard-motion`), pilih file dan bulan, klik **Publish**.
3. Service `worker` mengonversi ke COG (Web Mercator, JPEG + mask), memeriksa lewat TiTiler, lalu menjadikannya bulan aktif dan menghapus COG lama di `data/cog/`.

File mentah di `data/incoming/` tidak dihapus otomatis. `data/` tidak masuk git maupun image Docker.

Peta (MapLibre) mengambil bulan aktif dari `GET /api/orthophoto/current/` (`tile_url`, `bounds`, `minzoom`, `maxzoom`, `tile_size`). Untuk halaman peta baru: `{% include "layout/maplibre.html" %}` di blok `css`, buat peta dengan `createMap` dari `static/js/peta.js` (basemap + kredit), lalu pakai `addOrthophotoLayer` dan `OrthophotoControl` dari `static/js/orthophoto-layer.js` (contoh: `templates/dashboard/index.html`).

Tanpa antrean (untuk tes): `docker compose exec web python manage.py publish_orthophoto <file> --bulan 2026-11`.

Setelah mengubah kode di `orthophoto/`, restart worker: `docker compose restart worker`.

## Struktur UI (template Viho)

```
templates/
├─ layout/
│  ├─ base.html        ← kerangka halaman + blok page_title/breadcrumb/page_actions/content
│  ├─ head.html        ← meta, font, ikon (dipakai bersama halaman login)
│  ├─ theme_css.html   ← bootstrap + style Viho + static/css/besentry.css
│  ├─ core_js.html     ← jQuery, feather, sidebar, bootstrap
│  ├─ header.html      ← brand, search, notifikasi, dark mode, logout
│  ├─ sidebar.html     ← menu (tambah menu baru di sini)
│  └─ footer.html
├─ registration/login.html
└─ dashboard/
   ├─ index.html
   └─ partials/stat_card.html
static/
├─ assets/             ← aset Viho (CSS hasil compile, tanpa sass_processor)
└─ css/besentry.css    ← override tema BeSentry
```

Halaman baru cukup `{% extends "layout/base.html" %}` lalu isi blok `page_title`, `breadcrumb`, dan `content`.

## Menu sidebar

Menu disalin dari sidebar `/maps` di BeSentry Laravel (`resources/views/layouts/sidebarWmsAdmin.blade.php`) dan didefinisikan sebagai data di app `navigation`:

| File | Isi |
|---|---|
| `navigation/menu.py` | Urutan section dan item menu (`MAIN_MENU`, `MITRA_MENU`) |
| `navigation/pages.py` | Halaman yang belum dimigrasikan: URL, judul, dan nama route Laravel padanannya |
| `navigation/roles.py` | Nama role (Django Group) yang sama dengan role Laravel |

Role dibuat otomatis oleh migrasi: `admin-hazard-motion`, `hazard-motion-it-pama`, `hazard-motion-it-mtl`, `mitra-only`. Atur keanggotaannya di Admin > Groups. Superuser melihat semua menu.

Saat sebuah halaman sudah dibuat, ganti `page='...'` di `menu.py` menjadi `url_name='app:view'`, lalu hapus entrinya dari `pages.py`. `python manage.py test navigation` akan memeriksa link, ikon, dan aturan menu aktif.

Catatan:
- `feather.min.js` bawaan Viho versi lama. Ikon yang tidak ada (misalnya `hexagon`) menghentikan render semua ikon setelahnya. Cek dulu namanya di file tersebut.
- URL menu sidebar jangan `/`, karena penanda menu aktif memakai `indexOf` dan `/` akan cocok dengan semua halaman.

## Tes

```powershell
python manage.py test
```

## Konfigurasi

Salin `.env.example` ke `.env`, lalu isi `SECRET_KEY`.
