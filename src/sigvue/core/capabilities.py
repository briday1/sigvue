from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping, Protocol, TypeVar, runtime_checkable


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


@dataclass(frozen=True)
class AnnotationRequest:
    """Framework position plus plugin-defined values submitted by the user."""

    position_seconds: float
    duration_seconds: float | None = None
    values: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isfinite(self.position_seconds) or self.position_seconds < 0:
            raise ValueError("Annotation positions must be finite and non-negative")
        if self.duration_seconds is not None and (
            not isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("Annotation durations must be finite and non-negative")


@runtime_checkable
class DataAnnotator(Protocol[SourceData_contra, DeliveredData_contra]):
    """Optional plugin-owned annotation persistence contract."""

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


@runtime_checkable
class DataExporter(Protocol[SourceData_contra, DeliveredData_contra]):
    """Optional plugin-owned serialization contract."""

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
