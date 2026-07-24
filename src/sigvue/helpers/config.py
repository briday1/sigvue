"""Configuration helpers shared by workspace factories."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any


class WorkspaceConfig:
    """Typed access to the optional mapping received by a workspace factory."""

    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        self.values = MappingProxyType(dict(values or {}))

    def path(self, key: str, default: str | Path) -> Path:
        value = self.values.get(key, default)
        if not isinstance(value, (str, Path)):
            raise TypeError(f"{key} must be a path string")
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        base_value = self.values.get("profile_dir", Path.cwd())
        if not isinstance(base_value, (str, Path)):
            raise TypeError("profile_dir must be a path string")
        return Path(base_value).expanduser() / path

    def string(self, key: str, default: str) -> str:
        value = self.values.get(key, default)
        if not isinstance(value, str):
            raise TypeError(f"{key} must be a string")
        return value

    def floating(self, key: str, default: float) -> float:
        value = self.values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{key} must be numeric")
        return float(value)

    def integer(self, key: str, default: int) -> int:
        value = self.values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{key} must be an integer")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value: Any = self.values.get(key, default)
        if not isinstance(value, bool):
            raise TypeError(f"{key} must be true or false")
        return value


def configured_path(
    config: Mapping[str, object] | None,
    default: str | Path,
    *,
    key: str = "data_root",
) -> Path:
    """Return one expanded path from optional workspace configuration."""
    return WorkspaceConfig(config).path(key, default)
