"""Locate GDAL/GEOS/PROJ on Windows for GeoDjango.

Looks in the GDAL wheel inside the venv first (site-packages/osgeo),
then in an OSGeo4W install. Returns None on non-Windows platforms,
where Django finds the libraries on its own.
"""

import os
import re
import sys
from pathlib import Path

OSGEO4W_ROOT = Path(os.environ.get("OSGEO4W_ROOT", r"C:\OSGeo4W"))
GDAL_DLL_PATTERN = re.compile(r"^gdal\d*\.dll$", re.IGNORECASE)


def _find_gdal_dll(folder):
    if not folder.is_dir():
        return None
    matches = sorted(p for p in folder.iterdir() if GDAL_DLL_PATTERN.match(p.name))
    return matches[-1] if matches else None


def _candidates():
    wheel_dir = Path(sys.prefix) / "Lib" / "site-packages" / "osgeo"
    yield wheel_dir, wheel_dir / "data" / "proj"
    yield OSGEO4W_ROOT / "bin", OSGEO4W_ROOT / "share" / "proj"


def configure():
    """Return (gdal_dll, geos_dll) paths, or None if not on Windows."""
    if os.name != "nt":
        return None

    for bin_dir, proj_dir in _candidates():
        gdal_dll = _find_gdal_dll(bin_dir)
        geos_dll = bin_dir / "geos_c.dll"
        if gdal_dll and geos_dll.is_file():
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir};{os.environ['PATH']}"
            if proj_dir.is_dir():
                os.environ.setdefault("PROJ_LIB", str(proj_dir))
                os.environ.setdefault("PROJ_DATA", str(proj_dir))
            return str(gdal_dll), str(geos_dll)

    raise RuntimeError(
        "GDAL tidak ditemukan. Install wheel GDAL ke .venv atau OSGeo4W "
        f"di {OSGEO4W_ROOT}. Lihat README.md bagian 'Install GDAL'."
    )
