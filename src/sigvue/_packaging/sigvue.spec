# -*- mode: python ; coding: utf-8 -*-
"""One-file PyInstaller build for Sigvue."""

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


package_spec = importlib.util.find_spec("sigvue")
if package_spec is None or not package_spec.submodule_search_locations:
    raise RuntimeError("sigvue must be installed before building")
package_root = Path(next(iter(package_spec.submodule_search_locations))).resolve()
source_root = package_root.parent
datas = []
binaries = []
hiddenimports = collect_submodules("sigvue")

for package in ("sigvue", "plotly", "matplotlib", "numpy"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for distribution in (
    "sigvue",
    "plotly",
    "matplotlib",
    "numpy",
):
    try:
        datas += copy_metadata(distribution, recursive=True)
    except Exception:
        pass

a = Analysis(
    [str(package_root / "web" / "application.py")],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sigvue",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
