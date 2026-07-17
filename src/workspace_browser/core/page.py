from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .layout import LayoutNode, validate_layout
from .models import ItemDescriptor, RefreshConfiguration


@dataclass(frozen=True)
class PlaybackConfiguration:
    """Framework-owned playback lifecycle expressed only in elapsed time."""

    enabled: bool = False
    duration_seconds: float = 0.0
    step_seconds: float = 0.35
    loop: bool = True

    def __post_init__(self) -> None:
        if self.enabled and self.duration_seconds <= 0:
            raise ValueError("Playback duration must be positive")
        if self.step_seconds <= 0:
            raise ValueError("Playback step must be positive")


@dataclass(frozen=True)
class ControlSpec:
    name: str
    control_type: str
    default: object | None = None
    required: bool = False
    options: tuple[object, ...] = ()


@dataclass(frozen=True)
class ViewSpec:
    name: str
    callback: Callable[[dict[str, object]], object]


@dataclass(frozen=True)
class PageDefinition:
    title: str
    status: str
    views: tuple[ViewSpec, ...]
    layout: LayoutNode
    controls: tuple[ControlSpec, ...] = ()
    subtitle: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    actions: tuple[str, ...] = ()
    refresh: RefreshConfiguration = field(default_factory=RefreshConfiguration)
    playback: PlaybackConfiguration = field(default_factory=PlaybackConfiguration)

    def validate(self) -> None:
        view_names = {view.name for view in self.views}
        validate_layout(self.layout, view_names)


@dataclass(frozen=True)
class OpenedItem:
    item: ItemDescriptor
    page: PageDefinition
