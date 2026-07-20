from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Generic, Iterable, Literal, Mapping, TypeVar


SourceData_contra = TypeVar("SourceData_contra", contravariant=True)
DeliveredData_contra = TypeVar("DeliveredData_contra", contravariant=True)


@dataclass(frozen=True)
class CapabilityChoice:
    """One labeled value in a framework-rendered capability picker."""

    value: str
    label: str

    def __post_init__(self) -> None:
        if not self.value or not self.label:
            raise ValueError("Capability choices require a value and label")


@dataclass(frozen=True)
class AnnotationPlotBinding:
    """Populate an annotation input from one visible edge of a Plotly axis."""

    view: str
    axis: str
    edge: Literal["lower", "upper"]
    scale: float = 1.0
    offset: float = 0.0
    offset_source: Literal["none", "playback"] = "none"
    selection_policy: Literal["axis", "box_preferred"] = "axis"

    def __post_init__(self) -> None:
        if not self.view or not self.axis:
            raise ValueError("Plot bindings require a view and axis")
        if self.edge not in {"lower", "upper"}:
            raise ValueError(f"Unknown plot-bound edge: {self.edge}")
        if not isfinite(self.scale) or self.scale == 0 or not isfinite(self.offset):
            raise ValueError("Plot binding scale and offset must be finite, with a non-zero scale")
        if self.offset_source not in {"none", "playback"}:
            raise ValueError(f"Unknown plot binding offset source: {self.offset_source}")
        if self.selection_policy not in {"axis", "box_preferred"}:
            raise ValueError(f"Unknown plot binding selection policy: {self.selection_policy}")


@dataclass(frozen=True)
class AnnotationField:
    """One plugin-defined input in the annotation popover."""

    name: str
    label: str
    field_type: Literal["text", "textarea", "select", "number"] = "text"
    required: bool = False
    default: str = ""
    options: tuple[CapabilityChoice, ...] = ()
    plot_binding: AnnotationPlotBinding | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.label:
            raise ValueError("Annotation fields require a name and label")
        if self.field_type not in {"text", "textarea", "select", "number"}:
            raise ValueError(f"Unknown annotation field type: {self.field_type}")
        if self.field_type == "select" and not self.options:
            raise ValueError("Select annotation fields require options")


@dataclass(frozen=True)
class Annotation:
    """A plugin-discovered position or interval on the source timeline."""

    identifier: str
    start_seconds: float
    duration_seconds: float | None = None
    label: str | None = None
    comment: str | None = None
    frequency_lower_hz: float | None = None
    frequency_upper_hz: float | None = None
    view_selections: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.identifier:
            raise ValueError("Annotation identifiers cannot be empty")
        if not isfinite(self.start_seconds) or self.start_seconds < 0:
            raise ValueError("Annotation starts must be finite and non-negative")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("Annotation durations must be finite and non-negative")
        frequencies = (self.frequency_lower_hz, self.frequency_upper_hz)
        if any(value is None for value in frequencies) and any(value is not None for value in frequencies):
            raise ValueError("Annotation frequency bounds must provide both lower and upper edges")
        if all(value is not None for value in frequencies):
            lower, upper = frequencies
            if not isfinite(lower) or not isfinite(upper) or lower >= upper:
                raise ValueError("Annotation frequency bounds must be finite and increasing")
        if any(
            not isinstance(key, str)
            or not key.strip()
            or isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            for key, index in self.view_selections.items()
        ):
            raise ValueError("Annotation view selections require names and non-negative indexes")
        object.__setattr__(self, "view_selections", MappingProxyType(dict(self.view_selections)))


@dataclass(frozen=True)
class AnnotationRequest:
    """Framework position plus plugin-defined values submitted by the user."""

    position_seconds: float
    duration_seconds: float | None = None
    values: Mapping[str, str] = field(default_factory=dict)
    view_selections: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isfinite(self.position_seconds) or self.position_seconds < 0:
            raise ValueError("Annotation positions must be finite and non-negative")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("Annotation durations must be finite and non-negative")
        if any(not isinstance(key, str) or not key.strip() for key in self.values):
            raise ValueError("Annotation values require non-empty string names")
        if any(not isinstance(value, str) for value in self.values.values()):
            raise ValueError("Annotation values must be strings")
        if any(
            not isinstance(key, str)
            or not key.strip()
            or isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            for key, index in self.view_selections.items()
        ):
            raise ValueError("Annotation view selections require names and non-negative indexes")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "view_selections", MappingProxyType(dict(self.view_selections)))


class Annotator(ABC, Generic[SourceData_contra, DeliveredData_contra]):
    """Framework object for plugin-owned annotation persistence."""

    @property
    @abstractmethod
    def fields(self) -> tuple[AnnotationField, ...]: ...

    @abstractmethod
    def discover(self, source_data: SourceData_contra) -> Iterable[Annotation]: ...

    @abstractmethod
    def annotate(
        self,
        source_data: SourceData_contra,
        delivered_data: DeliveredData_contra,
        request: AnnotationRequest,
    ) -> Annotation: ...


@dataclass(frozen=True)
class ExportRequest:
    """A plugin-owned export selection made in the framework UI."""

    scope: str
    format: str
    control_values: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.scope or not self.format:
            raise ValueError("Export requests require a scope and format")


class Exporter(ABC, Generic[SourceData_contra, DeliveredData_contra]):
    """Framework object for plugin-owned serialization."""

    @property
    @abstractmethod
    def scopes(self) -> tuple[CapabilityChoice, ...]: ...

    @property
    @abstractmethod
    def formats(self) -> tuple[CapabilityChoice, ...]: ...

    @abstractmethod
    def export(
        self,
        source_data: SourceData_contra,
        delivered_data: DeliveredData_contra,
        request: ExportRequest,
        directory: Path,
    ) -> Path: ...


@dataclass(frozen=True)
class BatchRequest:
    """One plugin-defined action dispatched without opening an item view."""

    action: str

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("Batch requests require an action")


@dataclass(frozen=True)
class BatchResult:
    """Files and a concise completion message produced by a batch action."""

    files: tuple[Path, ...] = ()
    summary: str = "Completed"


class Batch(ABC, Generic[SourceData_contra]):
    """Plugin-owned item and workspace jobs run by the framework in background threads."""

    @property
    def item_actions(self) -> tuple[CapabilityChoice, ...]:
        return ()

    @property
    def workspace_actions(self) -> tuple[CapabilityChoice, ...]:
        return ()

    def run_item(
        self,
        resource: Any,
        source_data: SourceData_contra,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        raise NotImplementedError("This batch does not provide item actions")

    def run_workspace(
        self,
        resources: tuple[Any, ...],
        open_resource: Callable[[Any], SourceData_contra],
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        raise NotImplementedError("This batch does not provide workspace actions")


@dataclass(frozen=True)
class AnnotationCapability:
    fields: tuple[AnnotationField, ...]
    discover_callback: Callable[[], Iterable[Annotation]]
    annotate_callback: Callable[[dict[str, object], AnnotationRequest], Annotation]
    timeline_color_control: str | None = None


@dataclass(frozen=True)
class ExportCapability:
    scopes: tuple[CapabilityChoice, ...]
    formats: tuple[CapabilityChoice, ...]
    export_callback: Callable[[dict[str, object], ExportRequest, Path], Path]
