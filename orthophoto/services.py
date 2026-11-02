"""Publish a monthly orthophoto: raw GeoTIFF -> COG -> checked by TiTiler -> current month.

Each publish writes a COG with its own name (ortho_<YYYY-MM>_<id>.tif), so tile
URLs change every month and old tiles never linger in a cache. Once the new one
is current, the previous COG is deleted: only one orthophoto is kept on disk.
"""

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib.gis.geos import Polygon
from django.db import transaction
from django.utils import timezone

from .models import Orthophoto

RAW_EXTENSIONS = ('.tif', '.tiff')
TITILER_TIMEOUT = 60
TILE_MATRIX_SET = 'WebMercatorQuad'
TILE_SIZE = 256
ERROR_TAIL_CHARS = 2000


class PublishError(Exception):
    pass


def incoming_files():
    """Raw GeoTIFFs waiting in the incoming folder, newest first."""
    folder = settings.ORTHOPHOTO_INCOMING_DIR
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in RAW_EXTENSIONS]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def cog_name(ortho):
    return f'ortho_{ortho.bulan:%Y-%m}_{ortho.pk}.tif'


def titiler_path(ortho):
    """The COG's path as the TiTiler container sees it."""
    return f"{settings.TITILER_COG_DIR.rstrip('/')}/{ortho.file_cog}"


def _cog_query(ortho):
    return urllib.parse.urlencode({'url': titiler_path(ortho)})


def tile_url_template(ortho):
    """XYZ tile URL for MapLibre. WebP keeps the area outside the photo transparent."""
    return (
        f'{settings.TITILER_PUBLIC_URL}/cog/tiles/{TILE_MATRIX_SET}/{{z}}/{{x}}/{{y}}.webp'
        f'?{_cog_query(ortho)}'
    )


def preview_url(ortho):
    """TiTiler's own map page for a quick look at the COG."""
    return f'{settings.TITILER_PUBLIC_URL}/cog/{TILE_MATRIX_SET}/map.html?{_cog_query(ortho)}'


def gdal_translate_command(src, dst):
    return [
        'gdal_translate', str(src), str(dst),
        # The raw file is RGBA. JPEG holds at most 3 colour bands (plus a warp alpha it
        # can't store), so keep RGB and turn the alpha into a mask: outside the photo stays transparent.
        '-b', '1', '-b', '2', '-b', '3', '-mask', '4',
        '-of', 'COG',
        # Reproject to Web Mercator once here, so TiTiler serves tiles without warping.
        '-co', 'TILING_SCHEME=GoogleMapsCompatible',
        '-co', 'WARP_RESAMPLING=bilinear',
        '-co', 'OVERVIEW_RESAMPLING=average',
        '-co', 'COMPRESS=JPEG',
        '-co', 'QUALITY=85',
        '-co', 'BLOCKSIZE=512',
        '-co', 'BIGTIFF=YES',
        '-co', f'NUM_THREADS={settings.ORTHOPHOTO_GDAL_THREADS}',
        '--config', 'GDAL_CACHEMAX', str(settings.ORTHOPHOTO_GDAL_CACHEMAX_MB),
    ]


class ProgressParser:
    """Reads GDAL's terminal progress ("0...10...20...") one character at a time.

    GDAL prints each ten as it is reached and a dot every 2.5% in between ("0" at 0%,
    then dots at 2.5, 5 and 7.5, "10" at 10%...), so a ten counts as soon as its last
    digit arrives, and only dots that follow a ten or another dot count.
    """

    DOT_PERCENT = 2.5

    def __init__(self):
        self.digits = ''
        self.next_ten = 0
        self.in_line = False  # just after a ten or a progress dot
        self.value = 0.0
        self.last = -1

    def feed(self, char):
        """Return a new whole percentage when one is reached, else None."""
        if char.isdigit():
            self.digits += char
            if self.digits != str(self.next_ten):
                return None
            self.digits = ''
            self.value = self.next_ten
            self.next_ten += 10
            self.in_line = True
        elif char == '.' and self.in_line and not self.digits:
            self.value += self.DOT_PERCENT
        else:
            self.digits = ''
            self.in_line = False
            return None
        percent = int(self.value)
        if percent <= self.last:
            return None
        self.last = percent
        return percent


def convert_to_cog(src, dst, on_progress):
    process = subprocess.Popen(
        gdal_translate_command(src, dst), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    output = []
    parser = ProgressParser()
    try:
        while char := process.stdout.read(1):
            output.append(char)
            if (percent := parser.feed(char)) is not None:
                on_progress(percent)
        text = ''.join(output)
        # gdal_translate can exit 0 after failing to write every tile, so ERROR lines count too.
        if process.wait() != 0 or 'ERROR' in text:
            raise PublishError(text[-ERROR_TAIL_CHARS:].strip() or 'gdal_translate gagal.')
    finally:
        # A job timeout lands here too; don't leave gdal_translate running on its own.
        if process.poll() is None:
            process.kill()
            process.wait()


def read_tilejson(ortho):
    """Ask TiTiler for the new COG's TileJSON: proves it can read the file, and gives bounds and zooms."""
    url = f'{settings.TITILER_INTERNAL_URL}/cog/{TILE_MATRIX_SET}/tilejson.json?{_cog_query(ortho)}'
    try:
        with urllib.request.urlopen(url, timeout=TITILER_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors='replace')[:ERROR_TAIL_CHARS]
        raise PublishError(f'TiTiler tidak bisa membaca COG ({exc.code}): {body}') from exc
    except urllib.error.URLError as exc:
        raise PublishError(f'TiTiler tidak bisa dihubungi: {exc.reason}') from exc


def _update(ortho, **fields):
    for name, value in fields.items():
        setattr(ortho, name, value)
    Orthophoto.objects.filter(pk=ortho.pk).update(**fields)


def _make_current(ortho, tilejson):
    """Switch the current month to `ortho`; return the orthophoto it replaced, if any."""
    batas = Polygon.from_bbox(tilejson['bounds'])
    batas.srid = 4326
    with transaction.atomic():
        previous = Orthophoto.objects.select_for_update().filter(aktif=True).exclude(pk=ortho.pk).first()
        if previous:
            previous.aktif = False
            previous.status = Orthophoto.Status.DIGANTI
            previous.save(update_fields=['aktif', 'status'])
        ortho.aktif = True
        ortho.status = Orthophoto.Status.SIAP
        ortho.progres = 100
        ortho.batas = batas
        ortho.zoom_min = tilejson['minzoom']
        ortho.zoom_maks = tilejson['maxzoom']
        ortho.selesai = timezone.now()
        ortho.save(update_fields=[
            'aktif', 'status', 'progres', 'batas', 'zoom_min', 'zoom_maks', 'selesai',
        ])
    return previous


def publish(ortho_id, on_progress=None):
    """Convert, verify and switch to one orthophoto. On failure the current month is left untouched."""
    ortho = Orthophoto.objects.get(pk=ortho_id)
    src = settings.ORTHOPHOTO_INCOMING_DIR / ortho.file_sumber
    cog_dir = settings.ORTHOPHOTO_COG_DIR
    final = cog_dir / cog_name(ortho)
    # TiTiler only ever sees a finished file: write under another name, then rename.
    part = final.with_name(final.name + '.part')

    def progress(percent):
        _update(ortho, progres=percent)
        if on_progress:
            on_progress(percent)

    _update(ortho, status=Orthophoto.Status.DIPROSES, progres=0, file_cog=final.name, pesan_error='')
    try:
        if not src.is_file():
            raise PublishError(f'File sumber tidak ditemukan: {ortho.file_sumber}')
        cog_dir.mkdir(parents=True, exist_ok=True)
        convert_to_cog(src, part, progress)
        part.replace(final)
        previous = _make_current(ortho, read_tilejson(ortho))
    except Exception as exc:
        part.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        _update(
            ortho, status=Orthophoto.Status.GAGAL, pesan_error=str(exc) or type(exc).__name__,
            selesai=timezone.now(),
        )
        raise

    if previous and previous.file_cog:
        (cog_dir / previous.file_cog).unlink(missing_ok=True)
    return ortho
