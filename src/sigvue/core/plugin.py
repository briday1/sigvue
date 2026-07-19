from __future__ import annotations

from abc import abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Generic, Iterable, Iterator, Literal, Protocol, TypeVar, overload, runtime_checkable

from plotly.colors import get_colorscale

from .capabilities import (
    AnnotationCapability,
    DataAnnotator,
    DataExporter,
    ExportCapability,
)
from .layout import LayoutNode, container, control_slot, view_slot
from .models import ItemDescriptor, RefreshConfiguration, RefreshResult, WorkspaceMetadata
from .page import ControlSpec, OpenedItem, PageDefinition, PlaybackConfiguration, PlaybackMode, Segment, TimeUnit, ViewSpec


def _is_hex_color(value: str) -> bool:
    return len(value) == 7 and value.startswith("#") and all(character in "0123456789abcdefABCDEF" for character in value[1:])


@dataclass(frozen=True)
class DiscoveryColumn:
    """One plugin-defined, sortable column in the discovered-data browser."""

    key: str
    label: str
    kind: Literal["text", "number", "datetime", "si"] = "text"
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.label:
            raise ValueError("Discovery columns require a key and label")
        if self.kind not in {"text", "number", "datetime", "si"}:
            raise ValueError(f"Unknown discovery column kind: {self.kind}")
        if self.kind != "si" and self.unit is not None:
            raise ValueError("Only SI discovery columns accept a unit")


@dataclass(frozen=True)
class DataResource:
    """A discoverable input. Sources can keep their native reference in `source`."""

    identifier: str
    title: str
    source: Any
    subtitle: str | None = None
    timestamp: datetime | None = None
    tags: tuple[str, ...] = ()
    summary: dict[str, object | None] = field(default_factory=dict)
    navigation_path: tuple[str, ...] | None = None


SourceData_co = TypeVar("SourceData_co", covariant=True)
SourceData_contra = TypeVar("SourceData_contra", contravariant=True)
DeliveredData_co = TypeVar("DeliveredData_co", covariant=True)
SourceData = TypeVar("SourceData")
DeliveredData = TypeVar("DeliveredData")
LoadedData = TypeVar("LoadedData")


def _missing_methods(value: object, names: tuple[str, ...]) -> list[str]:
    return [f"{name}()" for name in names if not callable(getattr(value, name, None))]


@runtime_checkable
class DataSource(Protocol[SourceData_co]):
    """Typed source contract: discover resources, then open one source value."""

    @abstractmethod
    def discover(self) -> Iterable[DataResource]: ...

    @abstractmethod
    def open(self, resource: DataResource) -> SourceData_co: ...


@runtime_checkable
class DataDelivery(Protocol[SourceData_contra, DeliveredData_co]):
    """Typed delivery contract between source I/O and the analysis function."""

    @abstractmethod
    def prepare(self, source_data: SourceData_contra, ui: AnalysisContext) -> DeliveredData_co: ...


@dataclass(frozen=True)
class TraceStyle:
    """Framework-managed trace appearance with Plotly-ready properties."""

    line_style: str
    marker: str
    color: str
    width: float
    opacity: float = 1.0

    @property
    def mode(self) -> str:
        return "lines" if self.marker == "none" else "lines+markers"

    @property
    def line(self) -> dict[str, object]:
        return {"color": self.color_with_opacity(), "width": self.width, "dash": self.line_style}

    @property
    def plotly_marker(self) -> dict[str, object]:
        return {} if self.marker == "none" else {"color": self.color_with_opacity(), "symbol": self.marker}

    def color_with_opacity(self, color: str | None = None) -> str:
        """Return a Plotly color carrying this style's opacity."""
        selected = self.color if color is None else color
        if self.opacity >= 1 or not _is_hex_color(selected):
            return selected
        red, green, blue = (int(selected[index : index + 2], 16) for index in (1, 3, 5))
        return f"rgba({red},{green},{blue},{self.opacity:.3g})"


class DirectorySource(Generic[LoadedData]):
    """Framework-owned discovery for the common directory-of-inputs case."""

    def __init__(
        self,
        directory: str | Path,
        *,
        pattern: str | tuple[str, ...],
        loader: Callable[[Path], LoadedData],
        describe: Callable[[Path], DataResource] | None = None,
        recursive: bool = False,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.patterns = (pattern,) if isinstance(pattern, str) else pattern
        self.loader = loader
        self.describe = describe
        self.recursive = recursive

    def discover(self) -> list[DataResource]:
        paths = {
            path
            for pattern in self.patterns
            for path in (self.directory.rglob(pattern) if self.recursive else self.directory.glob(pattern))
            if path.is_file()
        }
        resources = []
        for path in sorted(paths):
            resource = self.describe(path) if self.describe else self._default_resource(path)
            if resource.navigation_path is None:
                parent = path.relative_to(self.directory).parent
                navigation_path = () if parent == Path(".") else parent.parts
                resource = replace(resource, navigation_path=navigation_path)
            resources.append(resource)
        return resources

    def open(self, resource: DataResource) -> LoadedData:
        return self.loader(Path(resource.source))

    def _default_resource(self, path: Path) -> DataResource:
        relative = path.relative_to(self.directory).as_posix()
        return DataResource(
            identifier=relative.replace("/", "::"),
            title=path.name,
            source=path,
            timestamp=datetime.fromtimestamp(path.stat().st_mtime),
            tags=(path.suffix.lstrip("."),) if path.suffix else (),
        )


@dataclass
class _Tab:
    label: str
    columns: int | tuple[float, ...]
    update: str
    nodes: list[LayoutNode] = field(default_factory=list)


class AnalysisContext:
    """Imperative UI hooks passed to an analysis function on every update."""

    def __init__(
        self,
        values: dict[str, object] | None = None,
        *,
        once_cache: dict[tuple[object, ...], object] | None = None,
        cache_lock: RLock | None = None,
    ) -> None:
        self.values = dict(values or {})
        self.controls: list[ControlSpec] = []
        self.figures: dict[str, object] = {}
        self.figure_updates: dict[str, str] = {}
        self.tabs: list[_Tab] = []
        self.playback_config = PlaybackConfiguration()
        self.refresh_config = RefreshConfiguration()
        self.metadata: dict[str, object] = {}
        self.statistics: dict[str, object] = {}
        self._delivered_data: object | None = None
        self._active_tab: _Tab | None = None
        self._active_nodes: list[LayoutNode] | None = None
        self._active_parameter_nodes: list[LayoutNode] | None = None
        self._active_switcher: tuple[str, list[LayoutNode]] | None = None
        self._active_switcher_view: tuple[str, str] | None = None
        self._switcher_keys: set[str] = set()
        self._once_cache = once_cache if once_cache is not None else {}
        self._cache_lock = cache_lock or RLock()

    @property
    def theme(self) -> str:
        """The browser's resolved color theme; plugins may use it or ignore it."""
        return str(self.values.get("__theme", "light"))

    @property
    def time(self) -> float:
        """Current framework playback time in seconds."""
        return float(self.values.get("__playback_time_seconds", 0.0))

    @property
    def following_live(self) -> bool:
        """Whether a live-capable playback request should use the newest buffer."""
        return str(self.values.get("__playback_follow_live", "false")).lower() in {"1", "true", "yes", "on"}

    def select(
        self,
        name: str,
        *,
        default: object,
        options: Iterable[object],
        label: str | None = None,
        group: str | None = None,
        picker: str | None = None,
        picker_label: str | None = None,
    ) -> object:
        choices = tuple(options)
        self._add_control(
            ControlSpec(
                name=name,
                control_type="select",
                label=label,
                default=default,
                options=choices,
                group=group,
                picker=picker,
                picker_label=picker_label,
            )
        )
        value = self.values.setdefault(name, default)
        if isinstance(default, bool):
            return str(value).lower() in {"1", "true", "yes", "on"}
        try:
            return type(default)(value)
        except (TypeError, ValueError):
            return default

    def toggle(
        self,
        name: str,
        *,
        default: bool = True,
        label: str | None = None,
        group: str | None = None,
    ) -> bool:
        """Add a compact on/off switch and return its current boolean value."""
        self._add_control(
            ControlSpec(
                name=name,
                control_type="toggle",
                label=label,
                default=bool(default),
                group=group,
            )
        )
        value = self.values.setdefault(name, default)
        return value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}

    def number(
        self,
        name: str,
        *,
        default: int | float,
        minimum: int | float | None = None,
        maximum: int | float | None = None,
        step: int | float | None = None,
        label: str | None = None,
        group: str | None = None,
        picker: str | None = None,
        picker_label: str | None = None,
    ) -> int | float:
        """Add an editable numeric input and return its current typed value."""
        control_type = "integer" if isinstance(default, int) and not isinstance(default, bool) else "float"
        self._add_control(
            ControlSpec(
                name=name,
                control_type=control_type,
                label=label,
                default=default,
                minimum=minimum,
                maximum=maximum,
                step=step,
                group=group,
                picker=picker,
                picker_label=picker_label,
            )
        )
        try:
            raw_value = self.values.setdefault(name, default)
            value = int(raw_value) if control_type == "integer" else float(raw_value)
        except (TypeError, ValueError):
            return default
        if minimum is not None:
            value = max(value, minimum)
        if maximum is not None:
            value = min(value, maximum)
        return value

    def color(
        self,
        name: str,
        *,
        default: str,
        label: str | None = None,
        group: str | None = None,
        picker: str | None = None,
        picker_label: str | None = None,
    ) -> str:
        """Add a native color-picker control and return a validated hex color."""
        if not _is_hex_color(default):
            raise ValueError("Color defaults must use #RRGGBB hex format")
        self._add_control(
            ControlSpec(
                name=name,
                control_type="color",
                label=label,
                default=default,
                group=group,
                picker=picker,
                picker_label=picker_label,
            )
        )
        value = str(self.values.setdefault(name, default))
        return value if _is_hex_color(value) else default

    def colormap(
        self,
        name: str,
        *,
        default: str,
        options: Iterable[str],
        label: str | None = None,
        group: str = "Plot styles",
    ) -> str:
        """Add a gradient-preview picker for a chosen set of Plotly colormaps."""
        choices = tuple(str(option) for option in options)
        if not choices:
            raise ValueError("Colormap options cannot be empty")
        if default not in choices:
            raise ValueError("The default colormap must be included in options")
        previews = []
        for choice in choices:
            try:
                previews.append(
                    tuple(f"{entry[1]} {float(entry[0]) * 100:g}%" for entry in get_colorscale(choice))
                )
            except Exception as error:
                raise ValueError(f"Unknown Plotly colormap: {choice}") from error
        self._add_control(
            ControlSpec(
                name=name,
                control_type="colormap",
                label=label,
                default=default,
                options=choices,
                group=group,
                option_previews=tuple(previews),
            )
        )
        value = str(self.values.setdefault(name, default))
        return value if value in choices else default

    def limits(
        self,
        name: str,
        *,
        default: tuple[float, float],
        minimum: float,
        maximum: float,
        step: float = 1.0,
        label: str | None = None,
        group: str = "Plot styles",
    ) -> tuple[float, float]:
        """Add paired numeric limits with editable boxes and a dual-handle bar."""
        lower_default, upper_default = (float(default[0]), float(default[1]))
        minimum, maximum, step = float(minimum), float(maximum), float(step)
        if minimum >= maximum:
            raise ValueError("Limits minimum must be less than maximum")
        if step <= 0:
            raise ValueError("Limits step must be positive")
        if not minimum <= lower_default < upper_default <= maximum:
            raise ValueError("Default limits must be ordered and within the available range")
        self._add_control(
            ControlSpec(
                name=name,
                control_type="limits",
                label=label,
                default=(lower_default, upper_default),
                minimum=minimum,
                maximum=maximum,
                step=step,
                group=group,
            )
        )
        raw = self.values.setdefault(name, (lower_default, upper_default))
        try:
            parts = raw.split(",", 1) if isinstance(raw, str) else raw
            lower, upper = float(parts[0]), float(parts[1])
        except (IndexError, TypeError, ValueError):
            return lower_default, upper_default
        lower = min(maximum, max(minimum, lower))
        upper = min(maximum, max(minimum, upper))
        return (lower, upper) if lower < upper else (lower_default, upper_default)

    def trace_style(
        self,
        name: str,
        *,
        label: str | None = None,
        color: str = "#087e8b",
        width: float = 1.5,
        line_style: str = "solid",
        marker: str = "none",
        opacity: float = 1.0,
        group: str = "Plot styles",
    ) -> TraceStyle:
        """Declare stored Details controls for one configurable plot trace."""
        if not 0 <= float(opacity) <= 1:
            raise ValueError("Trace opacity defaults must be between 0 and 1")
        prefix = label or name.replace("_", " ").title()
        selected_color = self.color(
            f"{name}_color",
            label="Color",
            default=color,
            group=group,
            picker=name,
            picker_label=prefix,
        )
        selected_width = float(
            self.number(
                f"{name}_width",
                label="Line width",
                default=float(width),
                minimum=0.5,
                maximum=10.0,
                step=0.5,
                group=group,
                picker=name,
                picker_label=prefix,
            )
        )
        selected_opacity = float(
            self.number(
                f"{name}_opacity",
                label="Opacity",
                default=float(opacity),
                minimum=0.0,
                maximum=1.0,
                step=0.05,
                group=group,
                picker=name,
                picker_label=prefix,
            )
        )
        selected_style = str(
            self.select(
                f"{name}_line_style",
                label="Line style",
                default=line_style,
                options=("solid", "dot", "dash", "dashdot"),
                group=group,
                picker=name,
                picker_label=prefix,
            )
        )
        selected_marker = str(
            self.select(
                f"{name}_marker",
                label="Marker",
                default=marker,
                options=("none", "circle", "square", "diamond", "cross", "x"),
                group=group,
                picker=name,
                picker_label=prefix,
            )
        )
        return TraceStyle(selected_style, selected_marker, selected_color, selected_width, selected_opacity)

    def _add_control(self, control: ControlSpec) -> None:
        if any(existing.name == control.name for existing in self.controls):
            raise ValueError(f"Duplicate control name: {control.name}")
        if self._active_parameter_nodes is not None:
            control = ControlSpec(
                name=control.name,
                control_type=control.control_type,
                label=control.label,
                placement="inline",
                default=control.default,
                required=control.required,
                options=control.options,
                minimum=control.minimum,
                maximum=control.maximum,
                step=control.step,
                group=control.group,
                picker=control.picker,
                picker_label=control.picker_label,
                option_previews=control.option_previews,
            )
            self._active_parameter_nodes.append(control_slot(control.name))
        self.controls.append(control)

    def playback(
        self,
        *,
        mode: PlaybackMode = "seek",
        duration: float = 0.0,
        step: float = 0.35,
        refresh_interval: float | None = None,
        loop: bool = True,
        time_unit: TimeUnit = "s",
    ) -> float:
        """Select static, seekable, or live-tail data delivery controls."""
        self.playback_config = PlaybackConfiguration(
            mode=mode,
            duration_seconds=duration,
            step_seconds=step,
            refresh_interval_seconds=refresh_interval,
            loop=loop,
            time_unit=time_unit,
        )
        if mode == "static":
            return 0.0
        return duration if mode == "live" and self.following_live else self.time

    def windowed(
        self,
        *,
        duration: float,
        default_window: float,
        overview: Iterable[float] | None = None,
        overview_label: str | None = None,
        minimum_window: float | None = None,
        step: float | None = None,
        time_unit: TimeUnit = "s",
    ) -> tuple[float, float]:
        """Select an interval, optionally drawn over a proportional 1D overview."""
        duration = float(duration)
        default_window = float(default_window)
        minimum = float(minimum_window if minimum_window is not None else (step or default_window / 20))
        if duration <= 0:
            raise ValueError("Windowed duration must be positive")
        if default_window <= 0:
            raise ValueError("Default window must be positive")
        minimum = min(duration, max(minimum, 1e-12))
        default_end = min(duration, max(minimum, default_window))
        try:
            start = float(self.values.get("__window_start_seconds", 0.0))
            end = float(self.values.get("__window_end_seconds", default_end))
        except (TypeError, ValueError):
            start, end = 0.0, default_end
        start = min(duration - minimum, max(0.0, start))
        end = min(duration, max(start + minimum, end))
        values = () if overview is None else tuple(float(value) for value in overview)
        self.playback_config = PlaybackConfiguration(
            mode="windowed",
            duration_seconds=duration,
            step_seconds=float(step or minimum),
            loop=False,
            window_start_seconds=start,
            window_end_seconds=end,
            minimum_window_seconds=minimum,
            overview_values=values,
            overview_label=overview_label,
            time_unit=time_unit,
        )
        return start, end

    def segmented(
        self,
        *,
        duration: float,
        segments: Iterable[Segment] | None = None,
        segment_duration: float | None = None,
        stride: float | None = None,
        default: str | None = None,
        time_unit: TimeUnit = "s",
    ) -> Segment:
        """Select one explicit or regularly generated interval for display."""
        duration = float(duration)
        if duration <= 0:
            raise ValueError("Segmented duration must be positive")
        if segments is not None and segment_duration is not None:
            raise ValueError("Provide explicit segments or segment_duration, not both")
        if segments is None:
            if segment_duration is None:
                raise ValueError("Segmented playback requires segments or segment_duration")
            segment_duration = float(segment_duration)
            stride = float(stride if stride is not None else segment_duration)
            if segment_duration <= 0 or segment_duration > duration:
                raise ValueError("Segment duration must be positive and within the recording")
            if stride <= 0:
                raise ValueError("Segment stride must be positive")
            count = int((duration - segment_duration) // stride) + 1
            descriptors = tuple(
                Segment(
                    identifier=f"segment-{index + 1}",
                    start_seconds=index * stride,
                    duration_seconds=segment_duration,
                    label=f"Segment {index + 1}",
                )
                for index in range(count)
            )
        else:
            descriptors = tuple(sorted(segments, key=lambda segment: segment.start_seconds))
        if not descriptors:
            raise ValueError("Segmented playback requires at least one segment")
        identifiers = {segment.identifier for segment in descriptors}
        requested = str(self.values.get("__segment_id", default or descriptors[0].identifier))
        if requested not in identifiers:
            requested = default if default in identifiers else descriptors[0].identifier
        selected = next(segment for segment in descriptors if segment.identifier == requested)
        self.playback_config = PlaybackConfiguration(
            mode="segmented",
            duration_seconds=duration,
            loop=False,
            segments=descriptors,
            selected_segment_id=selected.identifier,
            time_unit=time_unit,
        )
        return selected

    def refresh(self, *, every: float, timeout: float = 30.0) -> None:
        """Ask the framework to rerun this analysis for a live source."""
        self.refresh_config = RefreshConfiguration(enabled=True, interval_seconds=every, timeout_seconds=timeout)

    def stat(self, label: str, value: object) -> None:
        """Expose a workflow-defined value alongside framework timings."""
        self.statistics[label] = value

    def once(self, key: str, factory: Callable[[], Any], *, depends_on: Iterable[str] = ()) -> Any:
        """Compute and cache item-level work, optionally varying with named settings."""
        dependency_values = tuple((name, repr(self.values.get(name))) for name in depends_on)
        cache_key = ("value", key, dependency_values)
        with self._cache_lock:
            if cache_key not in self._once_cache:
                self._once_cache[cache_key] = factory()
            return self._once_cache[cache_key]

    @contextmanager
    def tab(
        self,
        label: str,
        *,
        columns: int | tuple[float, ...] = 1,
        update: str = "dynamic",
    ) -> Iterator[None]:
        """Group views and set their default static or dynamic update lifecycle."""
        self._validate_update(update)
        if isinstance(columns, int):
            if columns < 1:
                raise ValueError("columns must be positive")
        elif not columns or any(weight <= 0 for weight in columns):
            raise ValueError("column weights must be positive")
        tab = _Tab(label=label, columns=columns, update=update)
        self.tabs.append(tab)
        previous = self._active_tab
        previous_nodes = self._active_nodes
        self._active_tab = tab
        self._active_nodes = tab.nodes
        try:
            yield
        finally:
            self._active_tab = previous
            self._active_nodes = previous_nodes

    @contextmanager
    def group(self, direction: str = "column", **props: object) -> Iterator[None]:
        """Nest mixed views in a row, column, stack, or panel."""
        if self._active_parameter_nodes is not None:
            raise RuntimeError("Parameter groups may contain only number and select controls")
        if self._active_nodes is None:
            raise RuntimeError("ui.group() must be used inside ui.tab()")
        if direction not in {"row", "column", "stack", "panel"}:
            raise ValueError("direction must be 'row', 'column', 'stack', or 'panel'")
        parent = self._active_nodes
        children: list[LayoutNode] = []
        self._active_nodes = children
        try:
            yield
        except BaseException:
            self._active_nodes = parent
            raise
        else:
            self._active_nodes = parent
            parent.append(container(direction, children, **props))

    @contextmanager
    def parameter_group(self, label: str | None = None, *, columns: int = 1) -> Iterator[None]:
        """Place number/select controls directly inside the active tab layout."""
        if self._active_nodes is None:
            raise RuntimeError("ui.parameter_group() must be used inside ui.tab()")
        if self._active_parameter_nodes is not None:
            raise RuntimeError("Parameter groups cannot be nested")
        if columns < 1:
            raise ValueError("columns must be positive")
        parent = self._active_nodes
        children: list[LayoutNode] = []
        self._active_parameter_nodes = children
        try:
            yield
        finally:
            self._active_parameter_nodes = None
        if not children:
            raise ValueError("ui.parameter_group() must contain at least one number or select control")
        parent.append(container("control_group", children, label=label, columns=columns))

    @contextmanager
    def switcher(self, label: str, *, key: str, selector: str = "buttons") -> Iterator[None]:
        """Build a switcher whose choices may contain mixed framework layouts."""
        if self._active_nodes is None or self._active_tab is None:
            raise RuntimeError("ui.switcher() must be used inside ui.tab()")
        if self._active_switcher is not None:
            raise RuntimeError("ui.switcher() cannot be nested")
        if selector not in {"buttons", "dropdown"}:
            raise ValueError("selector must be 'buttons' or 'dropdown'")
        if key in self._switcher_keys:
            raise ValueError(f"Duplicate view-switcher key: {key}")
        parent = self._active_nodes
        choices: list[LayoutNode] = []
        self._active_switcher = (key, choices)
        self._switcher_keys.add(key)
        try:
            yield
        finally:
            self._active_switcher = None
        if not choices:
            raise ValueError("ui.switcher() must contain at least one ui.switcher_view()")
        parent.append(container("view_switcher", choices, label=label, key=key, selector=selector))

    @contextmanager
    def switcher_view(
        self,
        label: str,
        *,
        columns: int | tuple[float, ...] = 1,
    ) -> Iterator[None]:
        """Define one mixed-layout choice inside ui.switcher()."""
        if self._active_switcher is None:
            raise RuntimeError("ui.switcher_view() must be used inside ui.switcher()")
        if self._active_switcher_view is not None:
            raise RuntimeError("ui.switcher_view() cannot be nested")
        if isinstance(columns, int):
            if columns < 1:
                raise ValueError("columns must be positive")
        elif not columns or any(weight <= 0 for weight in columns):
            raise ValueError("column weights must be positive")
        key, choices = self._active_switcher
        parent = self._active_nodes
        children: list[LayoutNode] = []
        self._active_nodes = children
        self._active_switcher_view = (key, label)
        try:
            yield
        finally:
            self._active_nodes = parent
            self._active_switcher_view = None
        if not children:
            raise ValueError("ui.switcher_view() must contain at least one view")
        choices.append(container("grid", children, columns=columns, label=label))

    def view(
        self,
        value: object | Callable[[], object],
        *,
        key: str | None = None,
        update: str | None = None,
        depends_on: Iterable[str] = (),
    ) -> None:
        """Add any supported renderable: plot, table, text, markdown, or image."""
        if self._active_parameter_nodes is not None:
            raise RuntimeError("Parameter groups may contain only number and select controls")
        if self._active_tab is None or self._active_nodes is None:
            raise RuntimeError("ui.view() must be used inside ui.tab()")
        view_key = key or f"view-{len(self.figures) + 1}"
        if view_key in self.figures:
            raise ValueError(f"Duplicate view key: {view_key}")
        policy = update or self._active_tab.update
        self._validate_update(policy)
        self.figures[view_key] = self._resolve_figure(view_key, value, policy, depends_on)
        self.figure_updates[view_key] = policy
        self._active_nodes.append(view_slot(view_key))

    def plot(
        self,
        figure: object | Callable[[], object],
        *,
        key: str | None = None,
        update: str | None = None,
        depends_on: Iterable[str] = (),
    ) -> None:
        self.view(figure, key=key, update=update, depends_on=depends_on)

    def text(
        self,
        value: str | Callable[[], str],
        *,
        key: str | None = None,
        update: str | None = None,
        depends_on: Iterable[str] = (),
    ) -> None:
        self.view(value, key=key, update=update, depends_on=depends_on)

    def table(
        self,
        rows: object | Callable[[], object],
        *,
        key: str | None = None,
        update: str | None = None,
        depends_on: Iterable[str] = (),
    ) -> None:
        self.view(rows, key=key, update=update, depends_on=depends_on)

    def view_switcher(
        self,
        label: str,
        views: dict[str, object | Callable[[], object]],
        *,
        key: str,
        selector: str = "buttons",
        update: str | None = None,
        depends_on: Iterable[str] = (),
    ) -> None:
        """Add locally switchable views without creating tabs or analysis controls."""
        if self._active_tab is None:
            raise RuntimeError("ui.view_switcher() must be used inside ui.tab()")
        if selector not in {"buttons", "dropdown"}:
            raise ValueError("selector must be 'buttons' or 'dropdown'")
        if key in self._switcher_keys:
            raise ValueError(f"Duplicate view-switcher key: {key}")
        if not views:
            raise ValueError("View switchers require at least one view")
        policy = update or self._active_tab.update
        self._validate_update(policy)
        self._switcher_keys.add(key)
        slots = []
        for index, (view_label, figure) in enumerate(views.items()):
            view_key = f"{key}-{index}"
            if view_key in self.figures:
                raise ValueError(f"Duplicate view-switcher key: {view_key}")
            self.figures[view_key] = self._resolve_figure(view_key, figure, policy, depends_on)
            self.figure_updates[view_key] = policy
            slots.append(view_slot(view_key, label=view_label))
        if self._active_nodes is None:
            raise RuntimeError("ui.view_switcher() must be used inside ui.tab()")
        self._active_nodes.append(container("view_switcher", slots, label=label, key=key, selector=selector))

    @staticmethod
    def _validate_update(update: str) -> None:
        if update not in {"static", "dynamic"}:
            raise ValueError("update must be 'static' or 'dynamic'")

    def _resolve_figure(
        self,
        key: str,
        figure: object | Callable[[], object],
        update: str,
        depends_on: Iterable[str],
    ) -> object:
        factory = figure if callable(figure) else lambda: figure
        if update == "static":
            dependency_values = (("__theme", repr(self.values.get("__theme", "light"))),) + tuple((name, repr(self.values.get(name))) for name in depends_on)
            cache_key = ("view", key, dependency_values)
            with self._cache_lock:
                if cache_key not in self._once_cache:
                    self._once_cache[cache_key] = factory()
                return self._once_cache[cache_key]
        return factory()

    def layout(self) -> LayoutNode:
        tab_nodes = [
            container("grid", tab.nodes, label=tab.label, columns=tab.columns)
            for tab in self.tabs
        ]
        if not tab_nodes:
            raise ValueError("Analysis must add at least one plot")
        return tab_nodes[0] if len(tab_nodes) == 1 else container("tabs", tab_nodes)


class AnalysisWorkspace:
    """Adapts a source + analysis script to the full Workspace contract."""

    @overload
    def __init__(
        self,
        *,
        identifier: str,
        name: str,
        description: str,
        source: DataSource[SourceData],
        analyze: Callable[[SourceData, AnalysisContext], None],
        delivery: None = None,
        annotator: DataAnnotator[SourceData, SourceData] | None = None,
        exporter: DataExporter[SourceData, SourceData] | None = None,
        version: str = "0.1.0",
        category: str | None = None,
        tags: tuple[str, ...] = (),
        discovery_columns: tuple[DiscoveryColumn, ...] = (),
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        identifier: str,
        name: str,
        description: str,
        source: DataSource[SourceData],
        analyze: Callable[[DeliveredData, AnalysisContext], None],
        delivery: DataDelivery[SourceData, DeliveredData],
        annotator: DataAnnotator[SourceData, DeliveredData] | None = None,
        exporter: DataExporter[SourceData, DeliveredData] | None = None,
        version: str = "0.1.0",
        category: str | None = None,
        tags: tuple[str, ...] = (),
        discovery_columns: tuple[DiscoveryColumn, ...] = (),
    ) -> None: ...

    def __init__(
        self,
        *,
        identifier: str,
        name: str,
        description: str,
        source: DataSource[Any],
        analyze: Callable[[Any, AnalysisContext], None],
        delivery: DataDelivery[Any, Any] | None = None,
        annotator: DataAnnotator[Any, Any] | None = None,
        exporter: DataExporter[Any, Any] | None = None,
        version: str = "0.1.0",
        category: str | None = None,
        tags: tuple[str, ...] = (),
        discovery_columns: tuple[DiscoveryColumn, ...] = (),
    ) -> None:
        if not isinstance(source, DataSource):
            missing = _missing_methods(source, ("discover", "open"))
            detail = f"; missing {', '.join(missing)}" if missing else ""
            raise TypeError(f"source must implement DataSource.discover() and DataSource.open(resource){detail}")
        if delivery is not None and not isinstance(delivery, DataDelivery):
            missing = _missing_methods(delivery, ("prepare",))
            detail = f"; missing {', '.join(missing)}" if missing else ""
            raise TypeError(f"delivery must implement DataDelivery.prepare(source_data, ui){detail}")
        if annotator is not None and not isinstance(annotator, DataAnnotator):
            missing = _missing_methods(annotator, ("discover", "annotate"))
            detail = f"; missing {', '.join(missing)}" if missing else ""
            raise TypeError(f"annotator must implement DataAnnotator{detail}")
        if annotator is not None:
            field_names = [field.name for field in annotator.fields]
            if len(field_names) != len(set(field_names)):
                raise ValueError("DataAnnotator field names must be unique")
        if exporter is not None and not isinstance(exporter, DataExporter):
            missing = _missing_methods(exporter, ("export",))
            detail = f"; missing {', '.join(missing)}" if missing else ""
            raise TypeError(f"exporter must implement DataExporter{detail}")
        if exporter is not None:
            if not exporter.scopes or not exporter.formats:
                raise ValueError("DataExporter must provide at least one scope and format")
            for choice_kind, choices in (("scope", exporter.scopes), ("format", exporter.formats)):
                values = [choice.value for choice in choices]
                if len(values) != len(set(values)):
                    raise ValueError(f"DataExporter {choice_kind} values must be unique")
        if not callable(analyze):
            raise TypeError("analyze must be callable as analyze(delivered_data, ui)")
        if any(not isinstance(column, DiscoveryColumn) for column in discovery_columns):
            raise TypeError("discovery_columns must contain DiscoveryColumn values")
        column_keys = [column.key for column in discovery_columns]
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("Discovery column keys must be unique")
        self.metadata = WorkspaceMetadata(identifier, name, description, version, category, tags)
        self.discovery_columns = discovery_columns
        self.source = source
        self.analyze = analyze
        self.delivery = delivery
        self.annotator = annotator
        self.exporter = exporter
        self._once_caches: dict[str, dict[tuple[object, ...], object]] = {}
        self._cache_lock = RLock()

    def _resources(self) -> dict[str, DataResource]:
        discovered = list(self.source.discover())
        for index, resource in enumerate(discovered):
            if not isinstance(resource, DataResource):
                raise TypeError(
                    f"source.discover() item {index} must be DataResource, got {type(resource).__name__}"
                )
        resources = {resource.identifier: resource for resource in discovered}
        if len(resources) != len(discovered):
            duplicates = sorted(
                identifier
                for identifier in {resource.identifier for resource in discovered}
                if sum(resource.identifier == identifier for resource in discovered) > 1
            )
            raise ValueError(f"source.discover() returned duplicate identifiers: {', '.join(duplicates)}")
        return resources

    def discover_items(self) -> list[ItemDescriptor]:
        return [
            ItemDescriptor(
                identifier=resource.identifier,
                title=resource.title,
                subtitle=resource.subtitle,
                source_reference=str(resource.source),
                timestamp=resource.timestamp,
                tags=resource.tags,
                navigation_path=resource.navigation_path or (),
                summary_fields=resource.summary,
            )
            for resource in self._resources().values()
        ]

    def open_item(self, item_id: str) -> OpenedItem:
        return self.open_item_with_values(item_id, {})

    def open_item_with_values(self, item_id: str, values: dict[str, object]) -> OpenedItem:
        resources = self._resources()
        if item_id not in resources:
            raise KeyError(item_id)
        resource = resources[item_id]
        data = self.source.open(resource)
        cache: dict[tuple[tuple[str, str], ...], AnalysisContext] = {}
        with self._cache_lock:
            once_cache = self._once_caches.setdefault(item_id, {})

        def render(values: dict[str, object]) -> AnalysisContext:
            key = tuple(sorted((name, repr(value)) for name, value in values.items()))
            if key not in cache:
                context = AnalysisContext(values, once_cache=once_cache, cache_lock=self._cache_lock)
                started = perf_counter()
                prepared = self.delivery.prepare(data, context) if self.delivery is not None else data
                context._delivered_data = prepared
                self.analyze(prepared, context)
                context.statistics.setdefault("Analysis runtime", f"{(perf_counter() - started) * 1_000:.1f} ms")
                cache.clear()
                cache[key] = context
            return cache[key]

        initial = render(values)
        views = tuple(
            ViewSpec(name, lambda values, key=name: render(values).figures[key], initial.figure_updates[name])
            for name in initial.figures
        )
        item = next(item for item in self.discover_items() if item.identifier == item_id)
        page = PageDefinition(
            title=item.title,
            subtitle=item.subtitle,
            controls=tuple(initial.controls),
            views=views,
            layout=initial.layout(),
            playback=initial.playback_config,
            refresh=initial.refresh_config,
            metadata=initial.metadata,
            statistics=initial.statistics,
            annotation=(
                AnnotationCapability(
                    fields=tuple(self.annotator.fields),
                    discover_callback=lambda: tuple(self.annotator.discover(data)),
                    annotate_callback=lambda requested_values, request: self.annotator.annotate(
                        data, render(requested_values)._delivered_data, request
                    ),
                    timeline_color_control=getattr(self.annotator, "timeline_color_control", None),
                )
                if self.annotator is not None
                else None
            ),
            export=(
                ExportCapability(
                    scopes=tuple(self.exporter.scopes),
                    formats=tuple(self.exporter.formats),
                    export_callback=lambda requested_values, request, directory: self.exporter.export(
                        data, render(requested_values)._delivered_data, request, directory
                    ),
                )
                if self.exporter is not None
                else None
            ),
        )
        page.validate()
        return OpenedItem(item=item, page=page)

    def refresh_item(self, item_id: str) -> RefreshResult:
        return RefreshResult(changed=False)
