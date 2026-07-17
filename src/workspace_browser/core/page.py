from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .layout import LayoutNode, validate_layout
from .models import ItemDescriptor, RefreshConfiguration


PlaybackMode = Literal["static", "seek", "live"]


@dataclass(frozen=True)
class PlaybackConfiguration:
    """Framework-owned playback lifecycle expressed only in elapsed time."""

    mode: PlaybackMode = "static"
    duration_seconds: float = 0.0
    step_seconds: float = 0.35
    refresh_interval_seconds: float | None = None
    loop: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"static", "seek", "live"}:
            raise ValueError(f"Unknown playback mode: {self.mode}")
        if self.mode != "static" and self.duration_seconds < 0:
            raise ValueError("Playback duration cannot be negative")
        if self.step_seconds <= 0:
            raise ValueError("Playback step must be positive")
        if self.refresh_interval_seconds is not None and self.refresh_interval_seconds <= 0:
            raise ValueError("Playback refresh interval must be positive")


@dataclass(frozen=True)
class ControlSpec:
    name: str
    control_type: str
    default: object | None = None
    required: bool = False
    options: tuple[object, ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    label: str | None = None
    placement: str = "details"
    group: str | None = None
    picker: str | None = None
    picker_label: str | None = None


@dataclass(frozen=True)
class ViewSpec:
    name: str
    callback: Callable[[dict[str, object]], object]
    update_policy: str = "dynamic"


@dataclass(frozen=True)
class PageDefinition:
    title: str
    views: tuple[ViewSpec, ...]
    layout: LayoutNode
    controls: tuple[ControlSpec, ...] = ()
    subtitle: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    actions: tuple[str, ...] = ()
    refresh: RefreshConfiguration = field(default_factory=RefreshConfiguration)
    playback: PlaybackConfiguration = field(default_factory=PlaybackConfiguration)
    statistics: dict[str, object] = field(default_factory=dict)
    export_callback: Callable[[dict[str, object]], Any] | None = None

    def validate(self) -> None:
        invalid_placements = {control.placement for control in self.controls} - {"details", "inline"}
        if invalid_placements:
            raise ValueError(f"Unknown control placements: {', '.join(sorted(invalid_placements))}")
        invalid_updates = {view.update_policy for view in self.views} - {"static", "dynamic"}
        if invalid_updates:
            raise ValueError(f"Unknown view update policies: {', '.join(sorted(invalid_updates))}")
        view_names = {view.name for view in self.views}
        validate_layout(self.layout, view_names, {control.name for control in self.controls})


@dataclass(frozen=True)
class OpenedItem:
    item: ItemDescriptor
    page: PageDefinition
