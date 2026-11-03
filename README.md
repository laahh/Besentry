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
python manage.py migrate --database=log
python manage.py createsuperuser
```

Ada dua database: `besentry` (data utama) dan `besentry_log` (riwayat perubahan). Volume Docker baru membuat `besentry_log` otomatis. Untuk volume lama, buat sekali dengan `docker compose exec db createdb -U besentry besentry_log`.

## Menjalankan

```powershell
docker compose up -d
.\.venv\Scripts\Activate.ps1
python manage.py runserver          # http://127.0.0.1:8000/admin
```

Buka http://127.0.0.1:8000 (diarahkan ke `/login/`, lalu `/dashboard/`).

### Port sudah dipakai

Port di komputer bisa diubah lewat `.env` tanpa mengubah `docker-compose.yml`:

| Variabel | Default | Untuk |
|---|---|---|
| `WEB_PORT` | 8000 | Django (`web`) |
| `TITILER_PORT` | 8081 | TiTiler (URL tile untuk browser ikut menyesuaikan) |
| `DB_PORT` | 5433 | PostGIS |
| `REDIS_PORT` | 6379 | Redis |

Contoh: tambahkan `WEB_PORT=8001` ke `.env`, jalankan `docker compose up -d`, lalu buka http://localhost:8001. Kalau `DB_PORT`/`REDIS_PORT` diubah dan Django dijalankan dari `.venv`, sesuaikan juga port di `DATABASE_URL`/`REDIS_URL`.

## Orthophoto bulanan

Dikelola oleh **Survey & Geospatial Information**. Setiap peta di BeSentry mencantumkan kredit ini (lewat `createMap` di `static/js/peta.js`).

Orthophoto terbaru disajikan sebagai tile oleh TiTiler. Hanya satu orthophoto yang disimpan; publish bulan baru menggantikan yang lama.

1. Salin file mentah (GeoTIFF RGB + band alpha; nomor band alpha dicari otomatis) ke `data/incoming/`.
2. Buka **Spasial > Orthophoto** (`/orthophoto/`, khusus `admin-hazard-motion`), pilih file dan bulan, klik **Publish**.
3. Service `worker` mengonversi ke COG (Web Mercator, JPEG + mask), memeriksa lewat TiTiler, lalu menjadikannya bulan aktif dan menghapus COG lama di `data/cog/`.

File mentah di `data/incoming/` tidak dihapus otomatis. `data/` tidak masuk git maupun image Docker.

Peta (MapLibre) mengambil bulan aktif dari `GET /api/orthophoto/current/` (`tile_url`, `bounds`, `minzoom`, `maxzoom`, `tile_size`). Untuk halaman peta baru: `{% include "layout/maplibre.html" %}` di blok `css`, buat peta dengan `createMap` dari `static/js/peta.js` (basemap + kredit), lalu pakai `addOrthophotoLayer` dan `OrthophotoControl` dari `static/js/orthophoto-layer.js` (contoh: `templates/dashboard/index.html`).

Tanpa antrean (untuk tes): `docker compose exec web python manage.py publish_orthophoto <file> --bulan 2026-11`.

Setelah mengubah kode di `orthophoto/`, restart worker: `docker compose restart worker`.

## Login (BEATS + SID)

Pengguna masuk dengan **SID dan password BEATS**. Sesi, role, dan hak akses tetap disimpan di database BeSentry (`core/auth.py`):

1. Password dicek ke API login BEATS (`BEATS_LOGIN_URL`).
2. Jika benar, akun lokal dibuat/diperbarui (username = SID, nama, email) dan profil `core.Karyawan` diisi dari API SID: perusahaan, departemen, jabatan, site, status. Detail diambil ulang paling cepat tiap `SID_DETAIL_MAX_AGE_HOURS` (24 jam).
3. Karyawan dengan status SID selain `AKTIF` tidak bisa login dan akunnya dinonaktifkan.

| Kondisi | Hasil |
|---|---|
| Akun lokal bukan karyawan (admin, mitra) | Dicek dengan password lokal saja; **password-nya tidak pernah dikirim ke BEATS** |
| BEATS menolak password karyawan | Ditolak, password lokal lama tidak berlaku |
| BEATS tidak bisa diakses | Boleh login dengan password dari login sukses terakhir (`BEATS_OFFLINE_FALLBACK=True`); set `False` untuk menolak |
| Akun dinonaktifkan admin | Ditolak; login BEATS tidak mengaktifkannya kembali |

Isi `SID_API_KEY` di `.env` (header `x-api-key` API SID). Data pribadi yang tidak dibutuhkan (KTP, tanggal lahir, RFID, alamat) tidak disimpan.

**Sinkron massal** perusahaan dan karyawan (jalankan terjadwal, mis. tiap malam):

```powershell
python manage.py sync_karyawan                    # semua perusahaan + karyawan perusahaan yang dipakai BeSentry
python manage.py sync_karyawan --perusahaan 5194  # karyawan satu perusahaan (companyId SID)
python manage.py sync_karyawan --semua            # karyawan semua perusahaan (bisa puluhan ribu)
```

Perusahaan SID dicocokkan ke `Perusahaan` yang sudah ada (dari data CCTV) lewat nama, lalu ditautkan dengan `sid_id`. Karyawan yang tidak lagi `AKTIF` di SID langsung tidak bisa login.

## CCTV Database

`/cctv/database/` menggantikan spreadsheet CCTV per site. Semua site ada di satu tabel.

**Hak akses per site + perusahaan** (`cctv/access.py`):

| Siapa | Melihat | Tambah/ubah | Hapus | IP & password |
|---|---|---|---|---|
| Superuser, `admin-hazard-motion` | Semua | Semua | Ya | Superuser saja |
| Role dengan Coverage CCTV, mis. *CCTV BMO 2 - PT Pamapersada Nusantara* | Hanya Coverage CCTV-nya | Jika Coverage CCTV *Boleh tambah/ubah* | Tidak | Jika juga punya role IT |
| `hazard-motion-it-pama`, `hazard-motion-it-mtl` | Semua (Coverage CCTV bawaan migrasi, bisa dipersempit) | Ya | Tidak | Ya, dalam Coverage CCTV-nya |
| User tanpa Coverage CCTV | Menu tidak tampil, halaman 403 | | | |

Diatur di menu **Pengaturan** (khusus superuser dan `admin-hazard-motion`):

- **Role & Akses** (`/pengaturan/role/`): buat, ubah, atau hapus role dan Coverage CCTV-nya, serta buat role otomatis per site + perusahaan. Role bawaan sistem tidak bisa diganti nama atau dihapus.
- **Pengguna** (`/pengaturan/pengguna/`): cari pengguna, lalu centang role-nya. Admin tidak bisa melepas role admin dari akunnya sendiri.

Satu baris Coverage CCTV = satu site + perusahaan. Site atau perusahaan kosong berarti semua. Satu role boleh punya beberapa baris; user boleh punya beberapa role. CCTV di luar Coverage CCTV menjawab 404 (keberadaannya tidak terlihat).

Role per pasangan site + perusahaan juga bisa dibuat dari terminal (tambah `--bisa-ubah` jika role boleh mengubah):

```powershell
python manage.py buat_role_cctv
```

| Model | Isi |
|---|---|
| `core.Site`, `core.Perusahaan` | Master site dan perusahaan, dipakai semua modul |
| `core.Lokasi`, `core.DetailLokasi` | Master lokasi. `id` = ID dari sistem lokasi (mis. `1172174953`) |
| `cctv.ControlRoom` | Control room per site |
| `cctv.Cctv` | Satu baris per kamera. Posisi disimpan sebagai `PointField` |
| `cctv.CctvSpec` | 1:1, spesifikasi perangkat dan pemasangan (brand, model, radius, tinggi, tilt, azimuth) |
| `cctv.CctvAkses` | 1:1, NVR/IP/link/username/password. Hanya role IT; password terenkripsi |
| `cctv.CctvCoverage` | Polygon coverage per CCTV per minggu |

**Riwayat.** Setiap perubahan `Cctv`, `CctvSpec`, dan `CctvAkses` dicatat oleh django-simple-history di database `log` (app `cctv_log`, diarahkan oleh `config/db_router.py`), lengkap dengan user dan waktunya. Password tidak ikut dicatat. User disimpan sebagai id biasa (bukan FK) karena tabelnya ada di database lain.

**Password** dienkripsi dengan `FIELD_ENCRYPTION_KEY` di `.env`. **Backup kunci ini**: tanpa kunci, password yang tersimpan tidak bisa dibaca lagi. Password tidak pernah ikut di HTML; tombol *Tampilkan* mengambilnya satu per satu.

**Verifikasi coverage** (`/cctv/coverage/`, menu *Verifikasi Coverage*). Perubahan polygon coverage CCTV (Baru / Dipindah / Dicabut) melewati dua tahap review sebelum aktif (`cctv/coverage.py`):

1. Pengguna dengan Coverage CCTV *boleh ubah* mengajukan: pilih CCTV, lampirkan tangkapan layar, gambar polygon di peta (klik untuk titik, geser untuk mengubah, atau salin versi aktif).
2. Sistem memvalidasi geometri: polygon valid, di wilayah Berau, maks. 1.000 ha, titik terjauh ≤ radius kamera × 1,5 (2 km jika radius kosong).
3. **SGI** lalu **Safety** (`admin-hazard-motion`/superuser) mencentang semua checklist untuk menyetujui, atau mengembalikan dengan catatan wajib. Pengajuan yang dikembalikan direvisi lalu dikirim ulang.
4. Setelah Safety menyetujui, polygon menjadi `CctvCoverage` minggu ini (versi aktif). Untuk *Dicabut*, coverage minggu ini disimpan kosong.

**GeoPoint kamera** (panel di bawah perbandingan): menghubungkan tangkapan layar CCTV dengan peta lewat homografi (`static/js/homography.js`, `static/js/geopoint.js`).

- *Kalibrasi GCP*: pilih GCP, klik titiknya di gambar CCTV, lalu titik yang sama di peta orthophoto. Minimal 4 GCP; dengan 5+ GCP tampil RMSE (meter) dan error per GCP. Disimpan per pengajuan (`PengajuanCoverage.kalibrasi`), oleh pengaju atau reviewer selama belum selesai.
- *Titik*: klik permukaan tanah di gambar → pin + lat/lon di peta; klik peta → pin di gambar. Bisa disalin atau diekspor GeoJSON.
- *Overlay*: polygon usulan (dan versi aktif) diproyeksikan ke gambar CCTV, sehingga SGI melihat apakah area yang diusulkan benar-benar terlihat kamera.

Asumsinya permukaan tanah datar; di lereng atau beda elevasi besar akurasi menurun, jadi gunakan RMSE sebagai acuan.

**Verifikasi dari foto CCTV (homography Python)**: `cctv/homography.py` (OpenCV + NumPy).

- **Safety** mengkalibrasi kamera: pasangkan ≥ 4 GCP di tangkapan layar dan orthophoto, lalu *Simpan kalibrasi*. Server menghitung homography piksel → UTM 50N dengan RANSAC; GCP yang salah klik (> 3 m) dilaporkan sebagai outlier.
- Kalibrasi disimpan **per kamera** (`KalibrasiKamera`, satu aktif per CCTV) dan dipakai ulang untuk pengajuan berikutnya; otomatis nonaktif saat CCTV dipindah. Kalibrasi ulang jika kamera diputar.
- Analisis otomatis per pengajuan (`PengajuanCoverage.analisis`): **% coverage yang terlihat kamera** (bingkai foto diproyeksikan ke tanah, dipotong di cakrawala dan pada jangkauan = radius × 1,5), titik terjauh vs jangkauan, luas, RMSE. Dihitung ulang otomatis saat polygon berubah.
- **Gambar bukti** (`PengajuanCoverage.bukti`): foto CCTV dengan coverage yang terlihat dan GCP digambar, disimpan sebagai lampiran verifikasi.
- Tangkapan layar di-upload manual dari halaman verifikasi.

Satu CCTV hanya boleh punya satu pengajuan terbuka. Tangkapan layar disimpan di `MEDIA_ROOT/coverage/` dan hanya bisa dibuka lewat aplikasi (dicek hak aksesnya), bukan lewat URL publik.

**Notifikasi ke PIC SGI** (app `notifikasi`, menu *Pengaturan > PIC SGI*). Saat CCTV **ditambahkan** atau **lokasinya berubah** lewat form (site, lokasi, detail lokasi, lokasi pemasangan, atau koordinat bergeser ≥ 1 m), PIC SGI aktif untuk site itu (atau PIC "semua site") menerima WhatsApp lewat **Fonnte** dan email, berisi detail perubahan, link Google Maps, dan link ke CCTV. Import spreadsheet tidak memicu notifikasi.

- Kredensial di `.env`: `FONNTE_TOKEN`, `MAIL_BACKUP_*` (SMTP), `SITE_URL` (dasar link di pesan).
- Setiap pesan dicatat (`Notifikasi`: terkirim/gagal + alasannya). Dari halaman PIC SGI bisa *Tes kirim* dan *Kirim ulang*.
- Default dikirim langsung setelah data tersimpan. Di produksi set `NOTIFIKASI_ASYNC=True` dan jalankan service `notifier` (`python manage.py rqworker notifikasi`), supaya penyimpanan tidak menunggu Fonnte/SMTP.

**Import dari spreadsheet.** Download Google Sheets sebagai `.xlsx` (File > Download > Microsoft Excel), lalu:

```powershell
python manage.py import_cctv cctv.xlsx                  # semua tab site
python manage.py import_cctv cctv.xlsx --sheet "BC LMO"  # satu tab
```

Tab dikenali dari headernya (`No. CCTV`, `Fungsi CCTV`, `Kondisi`); tab ringkasan seperti `List CCTV` dan `BUMA-Old` dilewati. Nilai dinormalisasi (`FIX`/`FIXED`, `Mobile VIew`, spasi ganda, merek). Nilai yang tidak dikenal dikosongkan dan dilaporkan. Import bisa diulang: `No. CCTV` yang sudah ada diperbarui, dan baris yang tidak berubah tidak menambah riwayat.

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
