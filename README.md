# BeSentry

Django 5.2 + GeoDjango + PostGIS + Channels (realtime) + Celery.

## Stack

| Komponen | Versi | Jalan di |
|---|---|---|
| Python | 3.12 | `.venv` |
| Django | 5.2 LTS | `.venv` |
| PostgreSQL + PostGIS | 17 + 3.5 | Docker (`besentry_db`, port **5433**, karena 5432 dipakai PostgreSQL lokal) |
| Redis | 7 | Docker (`besentry_redis`, port 6379) |
| GDAL / GEOS / PROJ | 3.13 | wheel di `.venv` atau OSGeo4W |

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

Worker Celery (terminal terpisah, `--pool=solo` wajib di Windows):

```powershell
celery -A config worker --pool=solo -l info
```

Buka http://127.0.0.1:8000 (diarahkan ke `/login/`, lalu `/dashboard/`).

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
