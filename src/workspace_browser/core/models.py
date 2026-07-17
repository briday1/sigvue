from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

@dataclass(frozen=True)
class WorkspaceMetadata:
    identifier: str
    display_name: str
    description: str
    version: str
    category: str | None = None
    tags: tuple[str, ...] = ()
    icon: str | None = None

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("Workspace identifier must be non-empty")
        if not self.display_name.strip():
            raise ValueError("Workspace display_name must be non-empty")


@dataclass
class ItemDescriptor:
    identifier: str
    title: str
    source_reference: str | None = None
    subtitle: str | None = None
    timestamp: datetime | None = None
    tags: tuple[str, ...] = ()
    grouping_values: dict[str, str] = field(default_factory=dict)
    summary_fields: dict[str, str] = field(default_factory=dict)
    searchable_text: str | None = None

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("Item identifier must be non-empty")
        if not self.title.strip():
            raise ValueError("Item title must be non-empty")
@dataclass(frozen=True)
class RefreshConfiguration:
    enabled: bool = False
    interval_seconds: float | None = None
    min_interval_seconds: float = 0.5
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.interval_seconds is not None and self.interval_seconds < self.min_interval_seconds:
            raise ValueError("interval_seconds must be >= min_interval_seconds")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class RefreshResult:
    changed: bool = False
    metadata_changed: bool = False
    changed_views: tuple[str, ...] = ()
    all_views_changed: bool = False
    failed: bool = False
    next_suggested_refresh_at: datetime | None = None
