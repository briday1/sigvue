from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Iterable, Iterator, Protocol

from .layout import LayoutNode, container, view_slot
from .models import ItemDescriptor, RefreshConfiguration, RefreshResult, WorkspaceMetadata
from .page import ControlSpec, OpenedItem, PageDefinition, PlaybackConfiguration, ViewSpec


@dataclass(frozen=True)
class DataResource:
    """A discoverable input. Sources can keep their native reference in `source`."""

    identifier: str
    title: str
    source: Any
    subtitle: str | None = None
    timestamp: datetime | None = None
    tags: tuple[str, ...] = ()
    summary: dict[str, str] = field(default_factory=dict)


class DataSource(Protocol):
    """The only I/O contract required by the high-level plugin API."""

    def discover(self) -> Iterable[DataResource]: ...

    def open(self, resource: DataResource) -> Any: ...


class DirectorySource:
    """Framework-owned discovery for the common directory-of-inputs case."""

    def __init__(
        self,
        directory: str | Path,
        *,
        pattern: str | tuple[str, ...],
        loader: Callable[[Path], Any],
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
        return [self.describe(path) if self.describe else self._default_resource(path) for path in sorted(paths)]

    def open(self, resource: DataResource) -> Any:
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
        self._active_tab: _Tab | None = None
        self._active_nodes: list[LayoutNode] | None = None
        self._switcher_keys: set[str] = set()
        self._once_cache = once_cache if once_cache is not None else {}
        self._cache_lock = cache_lock or RLock()

    @property
    def time(self) -> float:
        """Current framework playback time in seconds."""
        return float(self.values.get("__playback_time_seconds", 0.0))

    def select(self, name: str, *, default: object, options: Iterable[object]) -> object:
        choices = tuple(options)
        self.controls.append(ControlSpec(name=name, control_type="select", default=default, options=choices))
        value = self.values.setdefault(name, default)
        if isinstance(default, bool):
            return str(value).lower() in {"1", "true", "yes", "on"}
        try:
            return type(default)(value)
        except (TypeError, ValueError):
            return default

    def number(
        self,
        name: str,
        *,
        default: int | float,
        minimum: int | float | None = None,
        maximum: int | float | None = None,
        step: int | float | None = None,
    ) -> int | float:
        """Add an editable numeric input and return its current typed value."""
        control_type = "integer" if isinstance(default, int) and not isinstance(default, bool) else "float"
        self.controls.append(
            ControlSpec(
                name=name,
                control_type=control_type,
                default=default,
                minimum=minimum,
                maximum=maximum,
                step=step,
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

    def playback(self, *, duration: float, step: float, refresh_interval: float | None = None, loop: bool = True) -> float:
        self.playback_config = PlaybackConfiguration(
            enabled=True,
            duration_seconds=duration,
            step_seconds=step,
            refresh_interval_seconds=refresh_interval,
            loop=loop,
        )
        return self.time

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

    def view(
        self,
        value: object | Callable[[], object],
        *,
        key: str | None = None,
        update: str | None = None,
        depends_on: Iterable[str] = (),
    ) -> None:
        """Add any supported renderable: plot, table, text, markdown, or image."""
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
            dependency_values = tuple((name, repr(self.values.get(name))) for name in depends_on)
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

    def __init__(
        self,
        *,
        identifier: str,
        name: str,
        description: str,
        source: DataSource,
        analyze: Callable[[Any, AnalysisContext], None],
        version: str = "0.1.0",
        category: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> None:
        self.metadata = WorkspaceMetadata(identifier, name, description, version, category, tags)
        self.source = source
        self.analyze = analyze
        self._once_caches: dict[str, dict[tuple[object, ...], object]] = {}
        self._cache_lock = RLock()

    def _resources(self) -> dict[str, DataResource]:
        return {resource.identifier: resource for resource in self.source.discover()}

    def discover_items(self) -> list[ItemDescriptor]:
        return [
            ItemDescriptor(
                identifier=resource.identifier,
                title=resource.title,
                subtitle=resource.subtitle,
                source_reference=str(resource.source),
                timestamp=resource.timestamp,
                tags=resource.tags,
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
                self.analyze(data, context)
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
        )
        page.validate()
        return OpenedItem(item=item, page=page)

    def refresh_item(self, item_id: str) -> RefreshResult:
        return RefreshResult(changed=False)
