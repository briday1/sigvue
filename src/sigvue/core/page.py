from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Callable, Iterator, Literal

from .capabilities import AnnotationCapability, ExportCapability
from .layout import LayoutNode, validate_layout
from .models import ItemDescriptor, RefreshConfiguration


PlaybackMode = Literal["static", "seek", "live", "windowed", "segmented"]
TimeUnit = Literal["auto", "samples", "ns", "us", "ms", "s", "min", "h", "d"]
AxisNavigation = Literal["free", "bounded"]


@dataclass(frozen=True)
class Segment:
    """One addressable interval on a segmented recording timeline."""

    identifier: str
    start_seconds: float
    duration_seconds: float
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.identifier:
            raise ValueError("Segment identifier cannot be empty")
        if not isfinite(self.start_seconds) or self.start_seconds < 0:
            raise ValueError("Segment start must be a finite non-negative time")
        if not isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("Segment duration must be finite and positive")


@dataclass(frozen=True)
class PlaybackConfiguration:
    """Framework-owned playback lifecycle with canonical seconds and a display unit."""

    mode: PlaybackMode = "static"
    duration_seconds: float = 0.0
    step_seconds: float = 0.35
    refresh_interval_seconds: float | None = None
    loop: bool = True
    window_start_seconds: float = 0.0
    window_end_seconds: float = 0.0
    minimum_window_seconds: float = 0.0
    overview_values: tuple[float, ...] = ()
    overview_series: tuple[tuple[float, ...], ...] = ()
    overview_durations_seconds: tuple[float, ...] = ()
    overview_switcher_key: str | None = None
    overview_label: str | None = None
    segments: tuple[Segment, ...] = ()
    selected_segment_id: str | None = None
    time_unit: TimeUnit = "s"

    def __post_init__(self) -> None:
        if self.mode not in {"static", "seek", "live", "windowed", "segmented"}:
            raise ValueError(f"Unknown playback mode: {self.mode}")
        if self.time_unit not in {"auto", "samples", "ns", "us", "ms", "s", "min", "h", "d"}:
            raise ValueError(f"Unknown timeline display unit: {self.time_unit}")
        if self.mode != "static" and self.duration_seconds < 0:
            raise ValueError("Playback duration cannot be negative")
        if self.step_seconds <= 0:
            raise ValueError("Playback step must be positive")
        if self.refresh_interval_seconds is not None and self.refresh_interval_seconds <= 0:
            raise ValueError("Playback refresh interval must be positive")
        if self.mode == "windowed":
            if self.duration_seconds <= 0:
                raise ValueError("Windowed duration must be positive")
            if not all(isfinite(value) for value in self.overview_values):
                raise ValueError("Windowed overview values must be finite")
            if not all(
                isfinite(value)
                for series in self.overview_series
                for value in series
            ):
                raise ValueError("Windowed overview series must be finite")
            if self.overview_series and not self.overview_switcher_key:
                raise ValueError("Windowed overview series require a view switcher key")
            if self.overview_switcher_key and not self.overview_series:
                raise ValueError("A windowed overview switcher requires overview series")
            if any(not series for series in self.overview_series):
                raise ValueError("Windowed overview series cannot be empty")
            if self.overview_durations_seconds:
                if len(self.overview_durations_seconds) != len(self.overview_series):
                    raise ValueError("Windowed overview durations must match the overview series")
                if not all(
                    isfinite(duration) and 0 < duration <= self.duration_seconds
                    for duration in self.overview_durations_seconds
                ):
                    raise ValueError(
                        "Windowed overview durations must be finite, positive, and within the full duration"
                    )
            if not 0 <= self.window_start_seconds < self.window_end_seconds <= self.duration_seconds:
                raise ValueError("Windowed selection must lie within the duration")
            if not 0 < self.minimum_window_seconds <= self.duration_seconds:
                raise ValueError("Windowed minimum size must be positive and within the duration")
        if self.mode == "segmented":
            if self.duration_seconds <= 0:
                raise ValueError("Segmented duration must be positive")
            if not self.segments:
                raise ValueError("Segmented playback requires at least one segment")
            identifiers = [segment.identifier for segment in self.segments]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("Segment identifiers must be unique")
            if any(segment.start_seconds + segment.duration_seconds > self.duration_seconds for segment in self.segments):
                raise ValueError("Segments must lie within the recording duration")
            if self.selected_segment_id not in identifiers:
                raise ValueError("Selected segment must identify an available segment")


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
    option_previews: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ViewSpec:
    name: str
    callback: Callable[[dict[str, object]], object]
    update_policy: str = "dynamic"
    axis_navigation: AxisNavigation = "free"
    dependencies: tuple[str, ...] = ()


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
    annotation: AnnotationCapability | None = None
    export: ExportCapability | None = None

    def validate(self) -> None:
        invalid_placements = {control.placement for control in self.controls} - {"details", "inline"}
        if invalid_placements:
            raise ValueError(f"Unknown control placements: {', '.join(sorted(invalid_placements))}")
        invalid_updates = {view.update_policy for view in self.views} - {"static", "dynamic"}
        if invalid_updates:
            raise ValueError(f"Unknown view update policies: {', '.join(sorted(invalid_updates))}")
        invalid_axis_navigation = {view.axis_navigation for view in self.views} - {"free", "bounded"}
        if invalid_axis_navigation:
            raise ValueError(
                f"Unknown axis-navigation policies: {', '.join(sorted(invalid_axis_navigation))}"
            )
        view_names = {view.name for view in self.views}
        validate_layout(self.layout, view_names, {control.name for control in self.controls})
        if self.playback.overview_switcher_key:
            switcher_keys = {
                str(node.props["key"])
                for node in _walk_layout(self.layout)
                if node.kind == "view_switcher" and node.props.get("key")
            }
            if self.playback.overview_switcher_key not in switcher_keys:
                raise ValueError(
                    "Windowed overview switcher must identify a view switcher in the page layout"
                )


@dataclass(frozen=True)
class OpenedItem:
    item: ItemDescriptor
    page: PageDefinition


def _walk_layout(node: LayoutNode) -> Iterator[LayoutNode]:
    yield node
    for child in node.children:
        yield from _walk_layout(child)
