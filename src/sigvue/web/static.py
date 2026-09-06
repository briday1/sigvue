"""Build a self-contained, static Sigvue site from explicitly selected assets."""

from __future__ import annotations

import argparse
import hashlib
from html import escape
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from sigvue.profile import tomllib


_PACKAGES = ("numpy", "scipy", "matplotlib", "pillow", "certifi")
_STDLIB_PACKAGES = {
    "ssl", "_ssl", "hashlib", "_hashlib", "lzma", "_lzma", "bz2", "_bz2",
    "sqlite3", "_sqlite3", "decimal", "_decimal", "zoneinfo", "_zoneinfo",
}
_RUNTIME_FILES = (
    "pyodide.js",
    "pyodide.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)
_CLIENT_FILES = ("static-client.js", "python-worker.js", "service-worker.js")
_EXCLUDED_DIRS = {
    "__pycache__", "tests", "test", "node_modules", "build", "dist",
}
_NATIVE_SUFFIXES = {".so", ".pyd", ".dll", ".dylib", ".exe"}


def _excluded(path: PurePosixPath) -> bool:
    return (
        any(
            part.startswith(".")
            or part in _EXCLUDED_DIRS
            or part.endswith(".egg-info")
            for part in path.parts
        )
        or path.suffix in {".pyc", ".pyo"}
        or (path.name.startswith("test_") and path.suffix == ".py")
    )


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _project_path(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    # Check the lexical path too: resolving first would hide symlink traversal.
    path = Path(os.path.abspath(path))
    if not _within(path, root) or not _within(path.resolve(), root):
        raise ValueError(f"Project path is outside --root: {value}")
    if any(part.is_symlink() for part in (path, *path.parents) if _within(part, root)):
        raise ValueError(f"Project paths must not traverse symlinks: {value}")
    if not path.exists():
        raise ValueError(f"Project path does not exist: {value}")
    return path


def _portable_path(value: object, base: Path, root: Path, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or value.startswith("~")
        or not _within((base / path).resolve(), root)
    ):
        raise ValueError(f"{label} must be portable and remain inside --root: {value}")
    lexical = Path(os.path.abspath(base / path))
    if any(part.is_symlink() for part in (lexical, *lexical.parents) if _within(part, root)):
        raise ValueError(f"{label} must not traverse symlinks: {value}")


def _profile(config: Path, root: Path) -> dict:
    payload = tomllib.loads(config.read_text(encoding="utf-8"))
    if not isinstance(payload.get("browser", {}), dict):
        raise ValueError("[browser] must be a table")
    for key in ("title", "subtitle"):
        value = payload.get("browser", {}).get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"browser.{key} must be a string")
    entries = payload.get("workspaces", [])
    if not isinstance(entries, list):
        raise ValueError("[[workspaces]] must be an array of tables")

    def check_configuration(values: dict, prefix: str) -> None:
        for key, value in values.items():
            label = f"{prefix}.{key}"
            if isinstance(value, dict):
                check_configuration(value, label)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        check_configuration(item, f"{label}[{index}]")
            elif key in {"path", "directory", "file"} or key.endswith(("_path", "_root", "_dir", "_file")):
                _portable_path(value, config.parent, root, label)

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("use"), str) or not entry["use"].strip():
            raise ValueError(f"Workspace {index + 1} requires a non-empty 'use'")
        if "path" in entry:
            _portable_path(entry["path"], config.parent, root, "Workspace path")
        configuration = entry.get("config", {})
        if not isinstance(configuration, dict):
            raise ValueError("Workspace config must be a table")
        check_configuration(configuration, "Workspace config")
    return payload


def _tree_files(directory: Path) -> Iterable[Path]:
    for current, directories, files in os.walk(directory, followlinks=False):
        parent = Path(current)
        directories[:] = sorted(
            name for name in directories
            if not _excluded(PurePosixPath(name)) and not (parent / name).is_symlink()
        )
        for name in sorted(files):
            path = parent / name
            if not path.is_symlink() and not _excluded(PurePosixPath(name)) and path.is_file():
                yield path


def _archive(destination: Path, files: dict[str, Path]) -> None:
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def _distribution_files(names: Iterable[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for name in names:
        distribution = metadata.distribution(name)
        distribution_root = Path(distribution.locate_file("")).resolve()
        if distribution.files is None:
            raise ValueError(f"Installed distribution has no file inventory: {name}")
        for entry in distribution.files:
            relative = PurePosixPath(str(entry))
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in str(relative)
                or _excluded(relative)
                or relative.suffix in _NATIVE_SUFFIXES
                or ".so." in relative.name
                or relative.name in {"direct_url.json", "RECORD"}
            ):
                continue
            # Distribution names need not match their modules (Plotly includes _plotly_utils).
            if relative.parts[0] in {"bin", "Scripts", "share", "include", "etc"} or relative.parts[0].endswith(".data"):
                continue
            path = Path(distribution.locate_file(entry))
            if (
                path.is_file()
                and _within(path.resolve(), distribution_root)
                and not any(
                    part.is_symlink()
                    for part in (path, *path.parents)
                    if _within(part, distribution_root)
                )
            ):
                files[relative.as_posix()] = path
    return files


def _runtime_files(runtime: Path) -> tuple[list[str], dict[str, Path]]:
    lock = json.loads((runtime / "pyodide-lock.json").read_text(encoding="utf-8"))
    available = lock["packages"]
    packages = list(_PACKAGES)
    # CPython's separately shipped extension modules are not in python_stdlib.zip.
    packages.extend(
        name for name, package in sorted(available.items())
        if (
            package.get("package_type") == "cpython_module"
            or package.get("install_dir") == "stdlib"
            or name in _STDLIB_PACKAGES
        )
        and name not in {"test", "tests"}
        and name not in packages
    )
    files = {name: runtime / name for name in _RUNTIME_FILES}
    core_module = "pyodide.asm.mjs" if (runtime / "pyodide.asm.mjs").is_file() else "pyodide.asm.js"
    files[core_module] = runtime / core_module
    visited: set[str] = set()

    def include(name: str) -> None:
        if name in visited:
            return
        if name not in available:
            raise ValueError(f"Pyodide runtime is missing package: {name}")
        visited.add(name)
        package = available[name]
        filename = package["file_name"]
        if not isinstance(filename, str) or Path(filename).name != filename or "\\" in filename or filename in {".", ".."}:
            raise ValueError(f"Unsafe Pyodide package filename: {filename}")
        files[filename] = runtime / filename
        for dependency in package.get("depends", []):
            include(dependency)

    for name in packages:
        include(name)
    for name, path in files.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Pyodide runtime file is missing or a symlink: {name}")
    return packages, files


def _index_html(profile: dict) -> str:
    from sigvue.web.application import _INDEX_HTML

    browser = profile.get("browser", {})
    html = _INDEX_HTML.replace("__BROWSER_TITLE__", escape(browser.get("title") or "Sigvue"))
    html = html.replace(
        "__BROWSER_SUBTITLE__",
        escape(browser.get("subtitle") or "Explore scientific and analytical results"),
    )
    html = html.replace('src="/assets/plotly.min.js"', 'src="assets/plotly.min.js"')
    if html.count("<script>") != 1:
        raise ValueError("The shared UI must contain exactly one inline application script")
    html = html.replace(
        "<script>",
        '<script src="static-client.js"></script>\n<script>\n'
        "(async function () {\nawait window.sigvueStatic.ready;\n",
        1,
    )
    return html.replace(
        "</script></body>",
        "\n})().catch(error => console.error(error));\n</script></body>",
        1,
    )


def build_static(
    *,
    root: str | Path,
    config: str | Path,
    includes: Iterable[str | Path],
    pyodide: str | Path,
    output: str | Path,
) -> Path:
    """Create a portable site without downloading files or importing user code."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("--root must be an existing directory")
    config_path = _project_path(config, root)
    if not config_path.is_file() or _excluded(PurePosixPath(config_path.relative_to(root).as_posix())):
        raise ValueError("--config must be a non-hidden, non-excluded file")
    selected = [_project_path(value, root) for value in includes]
    if not selected:
        raise ValueError("At least one explicit --include is required")
    destination = Path(output).expanduser().absolute()
    if destination.is_symlink():
        raise ValueError("--output must not be a symlink")
    destination = destination.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("--output must be absent or an empty directory; nothing will be deleted")
    for path in selected:
        if path.is_dir() and _within(destination, path):
            raise ValueError("--output must not be inside an included directory")
        if _excluded(PurePosixPath(path.relative_to(root).as_posix())):
            raise ValueError(f"Explicit include is excluded from archives: {path}")
    profile = _profile(config_path, root)
    project_files = {config_path.relative_to(root).as_posix(): config_path}
    for path in selected:
        for file in _tree_files(path) if path.is_dir() else (path,):
            project_files[file.relative_to(root).as_posix()] = file

    runtime_packages, runtime_files = _runtime_files(Path(pyodide).expanduser().resolve())
    framework_root = Path(__file__).resolve().parents[1]
    framework_files = {
        f"sigvue/{path.relative_to(framework_root).as_posix()}": path
        for path in _tree_files(framework_root)
    }
    python_files = _distribution_files(("plotly", "narwhals"))
    plotly_js = python_files.get("plotly/package_data/plotly.min.js")
    if plotly_js is None:
        raise ValueError("Installed Plotly distribution does not contain plotly.min.js")
    client_root = Path(__file__).parent / "static_assets"
    for name in _CLIENT_FILES:
        if not (client_root / name).is_file():
            raise ValueError(f"Sigvue static client asset is missing: {name}")
    html = _index_html(profile)

    destination.mkdir(parents=True, exist_ok=True)
    (destination / "runtime").mkdir()
    (destination / "assets").mkdir()
    for name, source in runtime_files.items():
        shutil.copyfile(source, destination / "runtime" / name)
    for name in _CLIENT_FILES:
        shutil.copyfile(client_root / name, destination / name)
    shutil.copyfile(plotly_js, destination / "assets" / "plotly.min.js")
    _archive(destination / "project.zip", project_files)
    _archive(destination / "framework.zip", framework_files)
    _archive(destination / "python-packages.zip", python_files)
    (destination / "index.html").write_text(html, encoding="utf-8")
    manifest = {
        "config": config_path.relative_to(root).as_posix(),
        "packages": runtime_packages,
        "project": "project.zip",
        "framework": "framework.zip",
        "pythonPackages": "python-packages.zip",
    }
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode())
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(destination).as_posix().encode() + b"\0")
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
    manifest["buildId"] = digest.hexdigest()
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Project root")
    parser.add_argument("--config", required=True, type=Path, help="Profile relative to root (included automatically)")
    parser.add_argument("--include", required=True, action="append", type=Path, help="Project file/directory to publish, relative to root; repeatable")
    parser.add_argument("--pyodide", required=True, type=Path, help="Extracted local Pyodide distribution")
    parser.add_argument("--output", required=True, type=Path, help="Absent or empty destination")
    arguments = parser.parse_args(argv)
    try:
        destination = build_static(
            root=arguments.root,
            config=arguments.config,
            includes=arguments.include,
            pyodide=arguments.pyodide,
            output=arguments.output,
        )
    except (OSError, ValueError, KeyError, metadata.PackageNotFoundError) as error:
        parser.error(str(error))
    print(f"Built static Sigvue site: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
