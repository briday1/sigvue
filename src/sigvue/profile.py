"""Configuration-driven workspace selection for the browser launcher."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from tempfile import NamedTemporaryFile
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
    metadata_overrides: dict[str, Any] = field(default_factory=dict)
    reference: str | None = None
    flatten_discovery: bool | None = None


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

    specs = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Workspace entry {index} must be a table")
        if entry.get("enabled", True) is False:
            continue
        specs.append(workspace_launch_spec(entry, profile_path.parent, index=index))

    return BrowserProfile(browser.get("title"), browser.get("subtitle"), tuple(specs))


def workspace_launch_spec(
    entry: dict[str, Any],
    profile_directory: str | Path,
    *,
    index: int | None = None,
) -> WorkspaceLaunchSpec:
    """Resolve one profile-shaped workspace entry for file or session use."""
    reference = entry.get("use")
    context = f"Workspace entry {index}" if index is not None else "Workspace"
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError(f"{context} requires a non-empty 'use'")
    reference = reference.strip()
    base = Path(profile_directory).expanduser().resolve()
    repository = _repository_path(entry.get("path"), base)
    available = _installed_entry_points()
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
    configuration.setdefault("profile_dir", str(base))
    _resolve_config_paths(configuration, base)
    flatten_discovery = entry.get("flatten_discovery")
    if flatten_discovery is not None and not isinstance(flatten_discovery, bool):
        raise ValueError(
            f"Workspace '{reference}' flatten_discovery must be true or false"
        )
    return WorkspaceLaunchSpec(
        module_name,
        attribute,
        configuration,
        repository,
        _workspace_metadata_overrides(entry, reference),
        reference,
        flatten_discovery,
    )


def workspace_factory_catalog(
    repository: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Describe workspace factories in the local project or environment.

    Discovery is intentionally scoped. Factories declared by the current (or
    explicitly selected) project are returned without mixing in unrelated
    globally installed workspaces. Installed entry points are a fallback when
    the current directory has no local workspace declarations.
    """
    root = (
        _repository_path(str(repository), Path.cwd())
        if repository is not None
        else Path.cwd().resolve()
    )
    assert root is not None
    local = _local_workspace_factory_catalog(root)
    if local or repository is not None:
        return local

    installed = _installed_entry_points()
    return [
        {
            "name": name,
            "use": name,
            "reference": entry_point.value.split("[", 1)[0].strip(),
            "package": entry_point.dist.name if entry_point.dist else None,
            "source": "installed",
            "repository": None,
            "defaults": {},
        }
        for name, entry_point in sorted(installed.items())
    ]


def append_workspace_to_profile(
    path: str | Path,
    spec: WorkspaceLaunchSpec,
) -> Path:
    """Atomically append one resolved workspace instance to a TOML profile."""
    target = Path(path).expanduser().resolve()
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    if existing.strip():
        payload = tomllib.loads(existing)
        entries = payload.get("workspaces", [])
        if not isinstance(entries, list):
            raise ValueError("[[workspaces]] entries are required")
        identifier = spec.metadata_overrides.get("identifier")
        if identifier and any(entry.get("id") == identifier for entry in entries):
            raise ValueError(
                f"Workspace '{identifier}' already exists in {target}"
            )

    block = _workspace_toml(spec)
    combined = existing
    if combined and not combined.endswith("\n"):
        combined += "\n"
    if combined.strip():
        combined += "\n"
    combined += block
    tomllib.loads(combined)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(combined)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_name = stream.name
        if target.exists():
            os.chmod(temporary_name, target.stat().st_mode)
        os.replace(temporary_name, target)
    finally:
        if temporary_name is not None and Path(temporary_name).exists():
            Path(temporary_name).unlink()
    return target


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


def _local_workspace_factory_catalog(repository: Path) -> list[dict[str, Any]]:
    """Find factory declarations owned by one source repository."""
    package_name = _repository_project_name(repository)
    candidates = (
        repository / "browser.toml",
        repository / "examples" / "browser.toml",
    )
    descriptors: list[dict[str, Any]] = []
    known_references: set[str] = set()
    visited_profiles: set[Path] = set()
    for candidate in candidates:
        profile_path = candidate.resolve()
        if profile_path in visited_profiles or not profile_path.is_file():
            continue
        visited_profiles.add(profile_path)
        profile = load_browser_profile(profile_path)
        for spec in profile.workspaces:
            reference = f"{spec.module_name}:{spec.attribute}"
            if reference in known_references:
                continue
            known_references.add(reference)
            overrides = spec.metadata_overrides
            use = spec.reference or reference
            configuration = {
                name: value
                for name, value in spec.configuration.items()
                if name not in {"profile_dir", "id", "name"}
            }
            defaults: dict[str, Any] = {
                "name": overrides.get("display_name"),
                "id": overrides.get("identifier"),
                "description": overrides.get("description"),
                "category": overrides.get("category"),
                "tags": list(overrides.get("tags", ())),
                "config": configuration,
                "flatten_discovery": spec.flatten_discovery,
            }
            descriptors.append(
                {
                    "name": overrides.get("display_name") or use,
                    "use": use,
                    "reference": reference,
                    "package": package_name,
                    "source": str(profile_path),
                    "repository": (
                        str(spec.watch_path) if spec.watch_path is not None else None
                    ),
                    "defaults": {
                        name: value
                        for name, value in defaults.items()
                        if value is not None and value != ""
                    },
                }
            )

    for name, entry_point in sorted(_repository_entry_points(repository).items()):
        reference = entry_point.value.split("[", 1)[0].strip()
        if reference in known_references:
            continue
        known_references.add(reference)
        descriptors.append(
            {
                "name": name,
                "use": name,
                "reference": reference,
                "package": package_name,
                "source": str(repository),
                "repository": str(repository),
                "defaults": {},
            }
        )
    return descriptors


def _repository_project_name(repository: Path) -> str | None:
    pyproject = repository / "pyproject.toml"
    if not pyproject.is_file():
        return None
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    name = payload.get("project", {}).get("name")
    return name if isinstance(name, str) else None


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


def _workspace_metadata_overrides(entry: dict[str, Any], reference: str) -> dict[str, Any]:
    """Validate browser-owned presentation metadata for one workspace instance."""
    overrides: dict[str, Any] = {}
    names = {
        "id": "identifier",
        "name": "display_name",
        "description": "description",
        "category": "category",
        "icon": "icon",
    }
    for source_name, target_name in names.items():
        if source_name not in entry:
            continue
        value = entry[source_name]
        if not isinstance(value, str):
            raise ValueError(f"Workspace '{reference}' {source_name} must be a string")
        if source_name in {"id", "name"} and not value.strip():
            raise ValueError(f"Workspace '{reference}' {source_name} must be non-empty")
        overrides[target_name] = value

    if "tags" in entry:
        tags = entry["tags"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ValueError(f"Workspace '{reference}' tags must be an array of non-empty strings")
        overrides["tags"] = tuple(tags)
    return overrides


_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(value: str) -> str:
    return value if _BARE_TOML_KEY.fullmatch(value) else _toml_string(value)


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\b", "\\b")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _toml_value(value: Any) -> str:
    if isinstance(value, Path):
        return _toml_string(str(value))
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("Non-finite workspace configuration is not supported")
        return repr(value)
    if isinstance(value, (list, tuple)):
        return f"[{', '.join(_toml_value(item) for item in value)}]"
    if isinstance(value, dict):
        values = ", ".join(
            f"{_toml_key(str(key))} = {_toml_value(item)}"
            for key, item in value.items()
        )
        return f"{{ {values} }}"
    raise ValueError(
        f"Unsupported workspace configuration value: {type(value).__name__}"
    )


def _workspace_toml(spec: WorkspaceLaunchSpec) -> str:
    overrides = spec.metadata_overrides
    reference = spec.reference or f"{spec.module_name}:{spec.attribute}"
    values: list[tuple[str, Any]] = [("use", reference)]
    if spec.watch_path is not None:
        values.append(("path", str(spec.watch_path)))
    if spec.flatten_discovery is not None:
        values.append(("flatten_discovery", spec.flatten_discovery))
    for key, source in (
        ("id", "identifier"),
        ("name", "display_name"),
        ("description", "description"),
        ("category", "category"),
        ("tags", "tags"),
        ("icon", "icon"),
    ):
        if source in overrides:
            values.append((key, overrides[source]))

    lines = ["[[workspaces]]"]
    lines.extend(f"{key} = {_toml_value(value)}" for key, value in values)
    configuration = {
        key: value
        for key, value in spec.configuration.items()
        if key not in {"profile_dir", "id", "name"}
    }
    if configuration:
        lines.append("")
        lines.append("[workspaces.config]")
        lines.extend(
            f"{_toml_key(str(key))} = {_toml_value(value)}"
            for key, value in configuration.items()
        )
    return "\n".join(lines) + "\n"
