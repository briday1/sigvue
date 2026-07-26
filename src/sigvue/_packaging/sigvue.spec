# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the shared Sigvue desktop host."""

import importlib.util
from importlib.metadata import version
from pathlib import Path
import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_entry_point,
    collect_submodules,
    copy_metadata,
)


package_spec = importlib.util.find_spec("sigvue")
if package_spec is None or not package_spec.submodule_search_locations:
    raise RuntimeError("sigvue must be installed before building")
package_root = Path(next(iter(package_spec.submodule_search_locations))).resolve()
source_root = package_root.parent
datas = []
binaries = []
hiddenimports = collect_submodules("sigvue")

for package in ("sigvue", "plotly", "matplotlib", "numpy", "webview"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

workspace_datas, workspace_imports = collect_entry_point("sigvue.workspaces")
datas += workspace_datas
hiddenimports += workspace_imports

for distribution in (
    "sigvue",
    "plotly",
    "matplotlib",
    "numpy",
    "pywebview",
):
    try:
        datas += copy_metadata(distribution, recursive=True)
    except Exception:
        pass

a = Analysis(
    [str(package_root / "web" / "desktop.py")],
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

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="sigvue-desktop",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    collected = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="sigvue-desktop",
    )
    app = BUNDLE(
        collected,
        name="Sigvue.app",
        version=version("sigvue"),
        bundle_identifier="com.sigvue.desktop",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="sigvue-desktop",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
