# -*- mode: python ; coding: utf-8 -*-
"""One-file PyInstaller build for Scientific Workspace Browser.

By default this downloads and bundles Chrome for Testing for Kaleido. Set
SWB_BUNDLE_CHROME=0 while building to rely on Chrome/Chromium on the target.
"""

from pathlib import Path
import os
import shutil

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


project_root = Path(SPECPATH).resolve()
source_root = project_root / "src"
build_support = project_root / ".pyinstaller"
build_support.mkdir(parents=True, exist_ok=True)

datas = []
binaries = []
hiddenimports = collect_submodules("workspace_browser")

# Plotly and Kaleido load portions of their implementation and resources
# dynamically. Explicit collection keeps those paths available when frozen.
for package in ("workspace_browser", "plotly", "kaleido", "choreographer"):
    package_datas, package_binaries, package_hidden = collect_all(
        package,
        filter_submodules=lambda name: not name.startswith("kaleido.mocker"),
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Entry-point discovery and runtime version checks use distribution metadata.
for distribution in (
    "workspace-browser",
    "plotly",
    "kaleido",
    "choreographer",
    "matplotlib",
    "numpy",
    "scipy",
):
    try:
        datas += copy_metadata(distribution, recursive=True)
    except Exception:
        # Some editable or distro-managed build environments omit metadata;
        # Analysis still collects the importable package itself.
        pass

runtime_hooks = []
bundle_chrome = os.environ.get("SWB_BUNDLE_CHROME", "1").lower() not in {"0", "false", "no", "off"}
if bundle_chrome:
    try:
        import certifi

        # Some framework Python installs do not inherit the operating system's
        # CA chain. Keep verification enabled while downloading Chrome.
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass

    import plotly.io as pio

    chrome_cache = build_support / "kaleido-chrome"
    chrome_cache.mkdir(parents=True, exist_ok=True)
    chrome_executable = Path(pio.get_chrome(chrome_cache)).resolve()
    relative_executable = chrome_executable.relative_to(chrome_cache)
    chrome_distribution_name = relative_executable.parts[0]

    # Treat Chrome as an opaque archive. On macOS its .app contains a nested
    # .framework that PyInstaller otherwise misclassifies during Analysis.
    chrome_archive = Path(
        shutil.make_archive(
            str(build_support / "kaleido_chrome"),
            "zip",
            root_dir=chrome_cache,
            base_dir=chrome_distribution_name,
        )
    )

    manifest = build_support / "kaleido_chrome_executable.txt"
    manifest.write_text(relative_executable.as_posix(), encoding="utf-8")
    datas += [
        (str(chrome_archive), "."),
        (str(manifest), "."),
    ]
    runtime_hooks.append(str(project_root / "packaging" / "runtime_hook_kaleido.py"))

a = Analysis(
    [str(source_root / "workspace_browser" / "web" / "application.py")],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
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
    name="workspace-browser",
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
