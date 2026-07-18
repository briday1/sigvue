"""Configuration-driven workspace selection for the browser launcher."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


ENTRY_POINT_GROUP = "sigvue.workspaces"


@dataclass(frozen=True)
class WorkspaceLaunchSpec:
    module_name: str
    attribute: str
    configuration: dict[str, Any] = field(default_factory=dict)
    watch_path: Path | None = None


@dataclass(frozen=True)
class BrowserProfile:
    title: str | None
    subtitle: str | None
    workspaces: tuple[WorkspaceLaunchSpec, ...]


def load_browser_profile(path: str | Path) -> BrowserProfile:
    """Load workspace factories and per-instance configuration from TOML."""
    profile_path = Path(path).expanduser().resolve()
    payload = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    browser = payload.get("browser", {})
    if not isinstance(browser, dict):
        raise ValueError("[browser] must be a table")
    entries = payload.get("workspaces", [])
    if not isinstance(entries, list):
        raise ValueError("[[workspaces]] entries are required")

    installed = _installed_entry_points()
    specs = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Workspace entry {index} must be a table")
        if entry.get("enabled", True) is False:
            continue
        reference = entry.get("use")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError(f"Workspace entry {index} requires a non-empty 'use'")

        repository = _repository_path(entry.get("path"), profile_path.parent)
        available = dict(installed)
        if repository is not None:
            _add_repository_import_paths(repository)
            available.update(_repository_entry_points(repository))
        module_name, attribute = _resolve_reference(reference, available)

        configuration = entry.get("config", {})
        if not isinstance(configuration, dict):
            raise ValueError(f"Workspace '{reference}' config must be a table")
        configuration = dict(configuration)
        for name in ("id", "name"):
            if name in entry:
                configuration.setdefault(name, entry[name])
        configuration.setdefault("profile_dir", str(profile_path.parent))
        _resolve_config_paths(configuration, profile_path.parent)
        specs.append(WorkspaceLaunchSpec(module_name, attribute, configuration, repository))

    return BrowserProfile(browser.get("title"), browser.get("subtitle"), tuple(specs))


def _installed_entry_points() -> dict[str, EntryPoint]:
    discovered = entry_points()
    selected = discovered.select(group=ENTRY_POINT_GROUP) if hasattr(discovered, "select") else discovered.get(ENTRY_POINT_GROUP, [])
    return {entry_point.name: entry_point for entry_point in selected}


def _repository_path(value: object, profile_directory: Path) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Workspace 'path' must be a non-empty repository directory")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = profile_directory / path
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"Workspace repository does not exist: {path}")
    return path


def _add_repository_import_paths(repository: Path) -> None:
    candidates = [repository / "src", repository]
    if (repository / "__init__.py").is_file():
        candidates.insert(0, repository.parent)
    for candidate in candidates:
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def _repository_entry_points(repository: Path) -> dict[str, EntryPoint]:
    pyproject = repository / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    values = payload.get("project", {}).get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
    if not isinstance(values, dict):
        return {}
    return {
        name: EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)
        for name, value in values.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def _resolve_reference(reference: str, available: dict[str, EntryPoint]) -> tuple[str, str]:
    value = available[reference].value if reference in available else reference
    value = value.split("[", 1)[0].strip()
    module_name, separator, attribute = value.partition(":")
    if not separator or not module_name or not attribute:
        known = ", ".join(sorted(available)) or "none"
        raise ValueError(f"Unknown workspace '{reference}'. Installed workspace names: {known}")
    return module_name, attribute


def _resolve_config_paths(configuration: dict[str, Any], profile_directory: Path) -> None:
    for key in ("data_root", "directory"):
        value = configuration.get(key)
        if isinstance(value, str):
            path = Path(value).expanduser()
            if not path.is_absolute():
                configuration[key] = str((profile_directory / path).resolve())
