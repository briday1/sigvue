"""Reusable, exact data discovery and buffering for Sigvue workspaces."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from inspect import Signature, signature
from math import isfinite
from pathlib import Path
from threading import RLock
from typing import Any, Generic, TypeVar, cast

from .page import PlaybackMode, Segment, TimeUnit
from .workspace import (
    BufferUI,
    DataResource,
)


Reference = TypeVar("Reference")
Opened = TypeVar("Opened")
Selected = TypeVar("Selected")

_MISSING = object()
_TIME_UNITS = {"auto", "samples", "ns", "us", "ms", "s", "min", "h", "d"}
_PLAYBACK_MODES = {"seek", "live"}


def _callable_signature(callback: Callable[..., object], label: str) -> Signature:
    if not callable(callback):
        raise TypeError(f"{label} must be callable")
    try:
        return signature(callback)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{label} must have an inspectable signature") from error


def _require_accepts(callback: Callable[..., object], count: int, label: str) -> None:
    callback_signature = _callable_signature(callback, label)
    try:
        callback_signature.bind(*([object()] * count))
    except TypeError as error:
        noun = "argument" if count == 1 else "arguments"
        raise TypeError(f"{label} must accept {count} positional {noun}") from error


def _require_arity(
    callback: Callable[..., object],
    count: int,
    label: str,
) -> None:
    """Validate the arity selected by the wrapper without banning defaults."""
    _require_accepts(callback, count, label)


def _positive_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{label} must be a number") from error
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def _validate_time_unit(time_unit: str) -> TimeUnit:
    if time_unit not in _TIME_UNITS:
        raise ValueError(f"Unknown timeline display unit: {time_unit}")
    return cast(TimeUnit, time_unit)


class Reader(Generic[Reference, Opened]):
    """Discover and open author-owned references, with optional buffering.

    ``discover`` returns the same references an ordinary script would use:
    paths, URLs, database keys, or domain descriptors.  ``describe`` is only
    needed to customize how those references appear in the browser.
    """

    def __init__(
        self,
        discover: Callable[[], Iterable[Reference]],
        open: Callable[[Reference], Opened],
        *,
        describe: Callable[[Reference], DataResource] | None = None,
        revision: Callable[[Reference], object] | None = None,
        cache_opened: bool = True,
    ) -> None:
        _require_accepts(discover, 0, "discover")
        _require_accepts(open, 1, "open")
        if describe is not None:
            _require_accepts(describe, 1, "describe")
        if revision is not None:
            _require_accepts(revision, 1, "revision")
        if not isinstance(cache_opened, bool):
            raise TypeError("cache_opened must be true or false")
        self._discover = discover
        self._open = open
        self._describe = describe
        self._revision = revision
        self._cache_opened = cache_opened
        self._resource_lock = RLock()
        self._resource_references: dict[str, Reference] = {}
        self._opened_cache: dict[str, tuple[object, Opened]] = {}
        self._opened_order: list[str] = []
        self._opened_revisions: dict[int, tuple[Opened, object]] = {}

    def discover(self) -> tuple[Reference, ...]:
        """Return native references exactly as a headless script consumes them."""
        discovered = self._discover()
        if isinstance(discovered, (str, bytes)):
            raise TypeError("discover must return an iterable of references, not text")
        try:
            return tuple(discovered)
        except TypeError as error:
            raise TypeError("discover must return an iterable of references") from error

    def open(self, reference: Reference) -> Opened:
        """Open one native reference."""
        return self._open(reference)

    def load(self, reference: Reference) -> Opened:
        """Open one native reference; mirrors buffered readers' ``load`` method."""
        return self.open(reference)

    def describe(self, reference: Reference) -> DataResource:
        """Describe one native reference for discovery and inspection."""
        if self._describe is not None:
            resource = self._describe(reference)
            if not isinstance(resource, DataResource):
                raise TypeError("describe must return a DataResource")
            return resource
        if isinstance(reference, DataResource):
            return reference
        if isinstance(reference, (str, Path)):
            path = Path(reference)
            title = path.name or str(reference)
            timestamp: datetime | None = None
            try:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                pass
            return DataResource(
                identifier=str(reference),
                title=title,
                source=reference,
                timestamp=timestamp,
                tags=(path.suffix.lstrip("."),) if path.suffix else (),
            )
        raise TypeError(
            "Reader.describe requires a describe callback for non-path references"
        )

    def resources(self) -> tuple[DataResource, ...]:
        """Return validated UI descriptions without opening the data."""
        return self._discover_resources()

    def clear_cache(self) -> None:
        """Discard recordings retained for repeated browser requests."""
        with self._resource_lock:
            self._opened_cache.clear()
            self._opened_order.clear()
            self._opened_revisions.clear()

    @classmethod
    def files(
        cls,
        directory: str | Path,
        pattern: str | Iterable[str],
        open: Callable[[Path], Opened],
        *,
        describe: Callable[[Path], DataResource] | None = None,
        revision: Callable[[Path], object] | None = None,
        cache_opened: bool = True,
        recursive: bool = False,
    ) -> Files[Opened]:
        """Create a reader for a directory of files.

        This is the convenient common path; ``Reader(...)`` remains available
        for URLs, database keys, grouped collections, and other native
        references.
        """
        return Files(
            directory,
            pattern,
            open,
            describe=describe,
            revision=revision,
            cache_opened=cache_opened,
            recursive=recursive,
        )

    def _prepare_for_ui(
        self,
        opened: Opened,
        ui: BufferUI,
    ) -> Opened:
        """Return the opened value when no browser-side buffering is configured."""
        return opened

    def windowed(
        self,
        read: Callable[[Opened, float, float], Selected],
        *,
        duration: float | Callable[[Opened], float],
        default: float,
        overview: Iterable[float] | Callable[[Opened], Iterable[float]] | None = None,
        overview_label: str | None = None,
        minimum: float | None = None,
        step: float | None = None,
        time_unit: TimeUnit = "s",
    ) -> WindowedReader[Reference, Opened, Selected]:
        """Add reusable ranged reads and a browser window selector."""
        return WindowedReader(
            self,
            read,
            duration=duration,
            default=default,
            overview=overview,
            overview_label=overview_label,
            minimum=minimum,
            step=step,
            time_unit=time_unit,
        )

    def segmented(
        self,
        read: Callable[[Opened, Segment], Selected],
        *,
        duration: float | Callable[[Opened], float],
        segments: Iterable[Segment] | Callable[[Opened], Iterable[Segment]] | None = None,
        segment_duration: float | None = None,
        stride: float | None = None,
        default: str | None = None,
        time_unit: TimeUnit = "s",
    ) -> SegmentedReader[Reference, Opened, Selected]:
        """Add explicit or regular segment reads and browser playback controls."""
        return SegmentedReader(
            self,
            read,
            duration=duration,
            segments=segments,
            segment_duration=segment_duration,
            stride=stride,
            default=default,
            time_unit=time_unit,
        )

    def playback(
        self,
        read: Callable[[Opened, float, float], Selected],
        *,
        duration: float | Callable[[Opened], float],
        default: float,
        minimum: float | None = None,
        maximum: float | Callable[[Opened], float] | None = None,
        buffer_step: float | None = None,
        mode: PlaybackMode = "seek",
        seek_step: float = 0.35,
        refresh_interval: float | None = None,
        loop: bool = False,
        time_unit: TimeUnit = "s",
    ) -> PlaybackReader[Reference, Opened, Selected]:
        """Add a seekable or live fixed-width buffer.

        ``read`` remains the same exact ranged-read function used by a script.
        Supplying ``maximum`` makes the buffer width adjustable in the browser.
        """
        return PlaybackReader(
            self,
            read,
            duration=duration,
            default=default,
            minimum=minimum,
            maximum=maximum,
            buffer_step=buffer_step,
            mode=mode,
            seek_step=seek_step,
            refresh_interval=refresh_interval,
            loop=loop,
            time_unit=time_unit,
        )

    def buffered(
        self,
        read: Callable[..., Selected],
        select: Callable[[Opened, BufferUI], Selected],
    ) -> BufferedReader[Reference, Opened, Selected]:
        """Add an application-specific exact buffer selector.

        ``read`` is the ordinary headless operation used by ``read()`` and
        ``load()``. ``select`` declares any browser controls it needs and
        returns the selected data. This is the escape hatch for buffering
        policies that do not fit regular window, segment, or playback helpers.
        """
        return BufferedReader(self, read, select)

    def _discover_resources(self) -> tuple[DataResource, ...]:
        references = self.discover()
        resources = tuple(self.describe(reference) for reference in references)
        identifiers = [resource.identifier for resource in resources]
        if any(not identifier for identifier in identifiers):
            raise ValueError("Discovered resource identifiers cannot be empty")
        duplicates = sorted(
            identifier
            for identifier in set(identifiers)
            if identifiers.count(identifier) > 1
        )
        if duplicates:
            raise ValueError(
                "discover returned duplicate resource identifiers: "
                + ", ".join(duplicates)
            )
        with self._resource_lock:
            self._resource_references = dict(zip(identifiers, references))
            available = set(identifiers)
            for identifier in tuple(self._opened_cache):
                if identifier not in available:
                    expired = self._opened_cache.pop(identifier, None)
                    if expired is not None:
                        self._opened_revisions.pop(id(expired[1]), None)
            self._opened_order = [
                identifier
                for identifier in self._opened_order
                if identifier in available
            ]
        return resources

    def _open_resource(self, resource: DataResource) -> Opened:
        with self._resource_lock:
            reference = self._resource_references.get(resource.identifier, _MISSING)
        if reference is _MISSING:
            self._discover_resources()
            with self._resource_lock:
                reference = self._resource_references.get(
                    resource.identifier, _MISSING
                )
        if reference is _MISSING:
            raise KeyError(resource.identifier)
        selected = cast(Reference, reference)
        if not self._cache_opened:
            opened = self.open(selected)
            revision = self._reference_revision(selected)
            with self._resource_lock:
                self._opened_revisions[id(opened)] = (opened, revision)
                while len(self._opened_revisions) > 64:
                    self._opened_revisions.pop(next(iter(self._opened_revisions)))
            return opened
        revision = self._reference_revision(selected)
        with self._resource_lock:
            cached = self._opened_cache.get(resource.identifier)
            if cached is not None and cached[0] == revision:
                if resource.identifier in self._opened_order:
                    self._opened_order.remove(resource.identifier)
                self._opened_order.append(resource.identifier)
                self._opened_revisions[id(cached[1])] = (cached[1], revision)
                return cached[1]
            opened = self.open(selected)
            if cached is not None and cached[1] is not opened:
                self._opened_revisions.pop(id(cached[1]), None)
            self._opened_cache[resource.identifier] = (revision, opened)
            self._opened_revisions[id(opened)] = (opened, revision)
            if resource.identifier in self._opened_order:
                self._opened_order.remove(resource.identifier)
            self._opened_order.append(resource.identifier)
            while len(self._opened_order) > 4:
                expired = self._opened_order.pop(0)
                expired_value = self._opened_cache.pop(expired, None)
                if expired_value is not None:
                    self._opened_revisions.pop(id(expired_value[1]), None)
            return opened

    def _reference_revision(self, reference: Reference) -> object:
        if self._revision is not None:
            return self._revision(reference)
        candidate = reference.source if isinstance(reference, DataResource) else reference
        if isinstance(candidate, (str, Path)):
            path = Path(candidate)
            try:
                status = path.stat()
            except OSError:
                return ("path", str(path))
            return ("path", str(path), status.st_mtime_ns, status.st_size)
        if isinstance(reference, DataResource):
            return (
                "resource",
                reference.identifier,
                repr(reference.timestamp),
                repr(reference.source),
            )
        return ("reference", repr(reference))

    def _opened_revision(self, opened: Opened) -> object:
        with self._resource_lock:
            entry = self._opened_revisions.get(id(opened))
        return entry[1] if entry is not None and entry[0] is opened else None


class Files(Reader[Path, Opened], Generic[Opened]):
    """Discover matching files while retaining an ordinary ``Path`` workflow."""

    def __init__(
        self,
        directory: str | Path,
        pattern: str | Iterable[str],
        open: Callable[[Path], Opened],
        *,
        describe: Callable[[Path], DataResource] | None = None,
        revision: Callable[[Path], object] | None = None,
        cache_opened: bool = True,
        recursive: bool = False,
    ) -> None:
        root = Path(directory).expanduser().resolve()
        patterns = (pattern,) if isinstance(pattern, str) else tuple(pattern)
        if not patterns or any(not isinstance(value, str) or not value for value in patterns):
            raise ValueError("Files requires at least one non-empty glob pattern")
        if not isinstance(recursive, bool):
            raise TypeError("recursive must be true or false")
        self.directory = root
        self.patterns = patterns
        self.recursive = recursive

        def discover_files() -> tuple[Path, ...]:
            paths = {
                path
                for selected_pattern in patterns
                for path in (
                    root.rglob(selected_pattern)
                    if recursive
                    else root.glob(selected_pattern)
                )
                if path.is_file()
            }
            return tuple(sorted(paths))

        def describe_file(path: Path) -> DataResource:
            if describe is not None:
                resource = describe(path)
                if not isinstance(resource, DataResource):
                    raise TypeError("describe must return a DataResource")
                return replace(resource, source=path)
            relative = path.relative_to(root)
            parent = relative.parent
            return DataResource(
                identifier=relative.as_posix().replace("/", "::"),
                title=path.name,
                source=path,
                timestamp=datetime.fromtimestamp(path.stat().st_mtime),
                tags=(path.suffix.lstrip("."),) if path.suffix else (),
                navigation_path=() if parent == Path(".") else parent.parts,
            )

        super().__init__(
            discover_files,
            open,
            describe=describe_file,
            revision=revision,
            cache_opened=cache_opened,
        )


class _RangeCache(Generic[Opened, Selected]):
    """A tiny identity cache that avoids rereading a buffer during tab changes."""

    def __init__(self, maximum: int = 2) -> None:
        self._maximum = maximum
        self._entries: list[tuple[Opened, object, float, float, Selected]] = []
        self._lock = RLock()

    def get(
        self,
        opened: Opened,
        revision: object,
        start: float,
        stop: float,
        load: Callable[[], Selected],
    ) -> Selected:
        with self._lock:
            for index, (candidate, version, left, right, value) in enumerate(
                self._entries
            ):
                if (
                    candidate is opened
                    and version == revision
                    and left == start
                    and right == stop
                ):
                    self._entries.append(self._entries.pop(index))
                    return value
        value = load()
        with self._lock:
            self._entries.append((opened, revision, start, stop, value))
            if len(self._entries) > self._maximum:
                self._entries.pop(0)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class BufferedReader(Generic[Reference, Opened, Selected]):
    """A Reader with author-defined exact headless and browser buffer reads."""

    def __init__(
        self,
        reader: Reader[Reference, Opened],
        read: Callable[..., Selected],
        select: Callable[[Opened, BufferUI], Selected],
    ) -> None:
        _require_accepts(read, 1, "buffer read")
        _require_arity(select, 2, "buffer select")
        self.reader = reader
        self._read = read
        self._select = select

    def discover(self) -> tuple[Reference, ...]:
        return self.reader.discover()

    def resources(self) -> tuple[DataResource, ...]:
        return self.reader.resources()

    def open(self, reference: Reference) -> Opened:
        return self.reader.open(reference)

    def read(self, opened: Opened, *args: object, **kwargs: object) -> Selected:
        """Read an exact author-defined selection from an opened item."""
        return self._read(opened, *args, **kwargs)

    def load(
        self,
        reference: Reference,
        *args: object,
        **kwargs: object,
    ) -> Selected:
        """Open a native reference and perform the same exact headless read."""
        return self.read(self.open(reference), *args, **kwargs)

    def clear_cache(self) -> None:
        self.reader.clear_cache()

    def _open_resource(self, resource: DataResource) -> Opened:
        return self.reader._open_resource(resource)

    def _opened_revision(self, opened: Opened) -> object:
        return self.reader._opened_revision(opened)

    def _prepare_for_ui(
        self,
        opened: Opened,
        ui: BufferUI,
    ) -> Selected:
        return self._select(opened, ui)


class WindowedReader(Generic[Reference, Opened, Selected]):
    """A Reader plus one explicit, reusable ranged-read operation."""

    def __init__(
        self,
        reader: Reader[Reference, Opened],
        read: Callable[[Opened, float, float], Selected],
        *,
        duration: float | Callable[[Opened], float],
        default: float,
        overview: Iterable[float] | Callable[[Opened], Iterable[float]] | None,
        overview_label: str | None,
        minimum: float | None,
        step: float | None,
        time_unit: TimeUnit,
    ) -> None:
        _require_arity(read, 3, "window read")
        if callable(duration):
            _require_accepts(duration, 1, "duration")
        else:
            duration = _positive_number(duration, "duration")
        default = _positive_number(default, "default window")
        if not callable(duration) and default > duration:
            raise ValueError("default window cannot exceed duration")
        if minimum is not None:
            minimum = _positive_number(minimum, "minimum window")
        if step is not None:
            step = _positive_number(step, "window step")
        if callable(overview):
            _require_accepts(overview, 1, "overview")
        elif overview is not None:
            overview = tuple(float(value) for value in overview)
            if not all(isfinite(value) for value in overview):
                raise ValueError("overview values must be finite")
        self.reader = reader
        self._read = read
        self._duration = duration
        self.default = default
        self.minimum = minimum
        self.step = step
        self.time_unit = _validate_time_unit(time_unit)
        self._overview = overview
        self.overview_label = overview_label
        self._overview_cache: list[tuple[Opened, object, tuple[float, ...]]] = []
        self._overview_lock = RLock()
        self._cache: _RangeCache[Opened, Selected] = _RangeCache()

    def discover(self) -> tuple[Reference, ...]:
        return self.reader.discover()

    def resources(self) -> tuple[DataResource, ...]:
        return self.reader.resources()

    def open(self, reference: Reference) -> Opened:
        return self.reader.open(reference)

    def duration(self, opened: Opened) -> float:
        value = self._duration(opened) if callable(self._duration) else self._duration
        return _positive_number(value, "duration")

    def overview(self, opened: Opened) -> tuple[float, ...]:
        revision = self.reader._opened_revision(opened)
        with self._overview_lock:
            for index, (candidate, version, values) in enumerate(
                self._overview_cache
            ):
                if candidate is opened and version == revision:
                    self._overview_cache.append(self._overview_cache.pop(index))
                    return values
        values = self._overview(opened) if callable(self._overview) else self._overview
        if values is None:
            return ()
        result = tuple(float(value) for value in values)
        if not all(isfinite(value) for value in result):
            raise ValueError("overview values must be finite")
        with self._overview_lock:
            self._overview_cache.append((opened, revision, result))
            if len(self._overview_cache) > 4:
                self._overview_cache.pop(0)
        return result

    def read(
        self,
        opened: Opened,
        start: float = 0.0,
        stop: float | None = None,
    ) -> Selected:
        """Read exactly the requested range from an already-opened item."""
        start, stop = self._range(opened, start, stop)
        return self._read(opened, start, stop)

    def load(
        self,
        reference: Reference,
        start: float = 0.0,
        stop: float | None = None,
    ) -> Selected:
        """Open a native reference and read exactly the requested range."""
        return self.read(self.open(reference), start, stop)

    def clear_cache(self) -> None:
        """Discard UI-opened recordings and selected buffers."""
        self.reader.clear_cache()
        self._cache.clear()
        with self._overview_lock:
            self._overview_cache.clear()

    def _open_resource(self, resource: DataResource) -> Opened:
        return self.reader._open_resource(resource)

    def _opened_revision(self, opened: Opened) -> object:
        return self.reader._opened_revision(opened)

    def _prepare_for_ui(
        self,
        opened: Opened,
        ui: BufferUI,
    ) -> Selected:
        return self._prepare(opened, ui)

    def _range(
        self,
        opened: Opened,
        start: float,
        stop: float | None,
    ) -> tuple[float, float]:
        total = self.duration(opened)
        try:
            left = float(start)
            right = min(total, left + self.default) if stop is None else float(stop)
        except (TypeError, ValueError) as error:
            raise TypeError("window start and stop must be numbers") from error
        if not isfinite(left) or not isfinite(right):
            raise ValueError("window start and stop must be finite")
        if not 0 <= left < right <= total:
            raise ValueError(
                f"window must satisfy 0 <= start < stop <= duration ({total:g})"
            )
        return left, right

    def _prepare(self, opened: Opened, ui: BufferUI) -> Selected:
        total = self.duration(opened)
        start, stop = ui.windowed(
            duration=total,
            default_window=min(self.default, total),
            overview=self.overview(opened) or None,
            overview_label=self.overview_label,
            minimum_window=self.minimum,
            step=self.step,
            time_unit=self.time_unit,
        )
        return self._cache.get(
            opened,
            self.reader._opened_revision(opened),
            start,
            stop,
            lambda: self._read(opened, start, stop),
        )


class SegmentedReader(Generic[Reference, Opened, Selected]):
    """A Reader plus named interval reads for headless and UI playback."""

    def __init__(
        self,
        reader: Reader[Reference, Opened],
        read: Callable[[Opened, Segment], Selected],
        *,
        duration: float | Callable[[Opened], float],
        segments: Iterable[Segment] | Callable[[Opened], Iterable[Segment]] | None,
        segment_duration: float | None,
        stride: float | None,
        default: str | None,
        time_unit: TimeUnit,
    ) -> None:
        _require_arity(read, 2, "segment read")
        if callable(duration):
            _require_accepts(duration, 1, "duration")
        else:
            duration = _positive_number(duration, "duration")
        if segments is not None and segment_duration is not None:
            raise ValueError("Provide segments or segment_duration, not both")
        if segments is None and segment_duration is None:
            raise ValueError("Segmented readers require segments or segment_duration")
        if callable(segments):
            _require_accepts(segments, 1, "segments")
        elif segments is not None:
            segments = tuple(segments)
        if segment_duration is not None:
            segment_duration = _positive_number(segment_duration, "segment duration")
        if stride is not None:
            stride = _positive_number(stride, "segment stride")
        if default is not None and not default:
            raise ValueError("default segment identifier cannot be empty")
        self.reader = reader
        self._read = read
        self._duration = duration
        self._segments = segments
        self.segment_duration = segment_duration
        self.stride = stride
        self.default = default
        self.time_unit = _validate_time_unit(time_unit)
        self._cache: _RangeCache[Opened, Selected] = _RangeCache()

    def discover(self) -> tuple[Reference, ...]:
        return self.reader.discover()

    def resources(self) -> tuple[DataResource, ...]:
        return self.reader.resources()

    def open(self, reference: Reference) -> Opened:
        return self.reader.open(reference)

    def duration(self, opened: Opened) -> float:
        value = self._duration(opened) if callable(self._duration) else self._duration
        return _positive_number(value, "duration")

    def segments(self, opened: Opened) -> tuple[Segment, ...]:
        total = self.duration(opened)
        if self._segments is None:
            segment_duration = cast(float, self.segment_duration)
            if segment_duration > total:
                raise ValueError("segment duration cannot exceed duration")
            stride = self.stride if self.stride is not None else segment_duration
            count = int((total - segment_duration) // stride) + 1
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
            values = self._segments(opened) if callable(self._segments) else self._segments
            descriptors = tuple(values)
        if not descriptors:
            raise ValueError("Segmented readers require at least one segment")
        if any(not isinstance(value, Segment) for value in descriptors):
            raise TypeError("segments must contain Segment values")
        descriptors = tuple(sorted(descriptors, key=lambda value: value.start_seconds))
        identifiers = [value.identifier for value in descriptors]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("segment identifiers must be unique")
        if any(
            value.start_seconds + value.duration_seconds > total
            for value in descriptors
        ):
            raise ValueError("segments must lie within the recording duration")
        if self.default is not None and self.default not in identifiers:
            raise ValueError("default segment identifier was not discovered")
        return descriptors

    def read(
        self,
        opened: Opened,
        segment: Segment | str | int | None = None,
    ) -> Selected:
        """Read one segment from an already-opened item."""
        selected = self._select(opened, segment)
        return self._read(opened, selected)

    def load(
        self,
        reference: Reference,
        segment: Segment | str | int | None = None,
    ) -> Selected:
        """Open a native reference and read one segment."""
        return self.read(self.open(reference), segment)

    def clear_cache(self) -> None:
        """Discard UI-opened recordings and selected segments."""
        self.reader.clear_cache()
        self._cache.clear()

    def _open_resource(self, resource: DataResource) -> Opened:
        return self.reader._open_resource(resource)

    def _opened_revision(self, opened: Opened) -> object:
        return self.reader._opened_revision(opened)

    def _prepare_for_ui(
        self,
        opened: Opened,
        ui: BufferUI,
    ) -> Selected:
        return self._prepare(opened, ui)

    def _select(
        self,
        opened: Opened,
        requested: Segment | str | int | None,
    ) -> Segment:
        segments = self.segments(opened)
        if isinstance(requested, Segment):
            if requested.identifier not in {value.identifier for value in segments}:
                raise KeyError(requested.identifier)
            return next(
                value for value in segments if value.identifier == requested.identifier
            )
        if isinstance(requested, int):
            try:
                return segments[requested]
            except IndexError as error:
                raise IndexError("segment index is out of range") from error
        identifier = requested or self.default or segments[0].identifier
        try:
            return next(value for value in segments if value.identifier == identifier)
        except StopIteration as error:
            raise KeyError(identifier) from error

    def _prepare(self, opened: Opened, ui: BufferUI) -> Selected:
        segments = self.segments(opened)
        selected = ui.segmented(
            duration=self.duration(opened),
            segments=segments,
            default=self.default,
            time_unit=self.time_unit,
        )
        start = selected.start_seconds
        stop = start + selected.duration_seconds
        return self._cache.get(
            opened,
            (
                self.reader._opened_revision(opened),
                selected.identifier,
            ),
            start,
            stop,
            lambda: self._read(opened, selected),
        )


class PlaybackReader(Generic[Reference, Opened, Selected]):
    """A Reader plus an exact moving range for seek or live playback."""

    def __init__(
        self,
        reader: Reader[Reference, Opened],
        read: Callable[[Opened, float, float], Selected],
        *,
        duration: float | Callable[[Opened], float],
        default: float,
        minimum: float | None,
        maximum: float | Callable[[Opened], float] | None,
        buffer_step: float | None,
        mode: PlaybackMode,
        seek_step: float,
        refresh_interval: float | None,
        loop: bool,
        time_unit: TimeUnit,
    ) -> None:
        _require_arity(read, 3, "playback read")
        if callable(duration):
            _require_accepts(duration, 1, "duration")
        else:
            duration = _positive_number(duration, "duration")
        if callable(maximum):
            _require_accepts(maximum, 1, "maximum buffer")
        elif maximum is not None:
            maximum = _positive_number(maximum, "maximum buffer")
        default = _positive_number(default, "default buffer")
        minimum = (
            default
            if minimum is None
            else _positive_number(minimum, "minimum buffer")
        )
        buffer_step = (
            minimum
            if buffer_step is None
            else _positive_number(buffer_step, "buffer step")
        )
        seek_step = _positive_number(seek_step, "seek step")
        if refresh_interval is not None:
            refresh_interval = _positive_number(
                refresh_interval,
                "refresh interval",
            )
        if mode not in _PLAYBACK_MODES:
            raise ValueError("Playback mode must be 'seek' or 'live'")
        if not isinstance(loop, bool):
            raise TypeError("loop must be true or false")
        if (
            not callable(maximum)
            and maximum is not None
            and not minimum <= default <= maximum
        ):
            raise ValueError(
                "Playback buffer must satisfy minimum <= default <= maximum"
            )
        self.reader = reader
        self._read = read
        self._duration = duration
        self.default = default
        self.minimum = minimum
        self._maximum = maximum
        self.buffer_step = buffer_step
        self.mode = mode
        self.seek_step = seek_step
        self.refresh_interval = refresh_interval
        self.loop = loop
        self.time_unit = _validate_time_unit(time_unit)
        self._cache: _RangeCache[Opened, Selected] = _RangeCache()

    def discover(self) -> tuple[Reference, ...]:
        return self.reader.discover()

    def resources(self) -> tuple[DataResource, ...]:
        return self.reader.resources()

    def open(self, reference: Reference) -> Opened:
        return self.reader.open(reference)

    def duration(self, opened: Opened) -> float:
        value = self._duration(opened) if callable(self._duration) else self._duration
        return _positive_number(value, "duration")

    def maximum(self, opened: Opened) -> float:
        value = (
            self._maximum(opened)
            if callable(self._maximum)
            else self._maximum
        )
        return self.default if value is None else _positive_number(
            value,
            "maximum buffer",
        )

    def read(
        self,
        opened: Opened,
        start: float = 0.0,
        stop: float | None = None,
    ) -> Selected:
        start, stop = self._range(opened, start, stop)
        return self._read(opened, start, stop)

    def load(
        self,
        reference: Reference,
        start: float = 0.0,
        stop: float | None = None,
    ) -> Selected:
        return self.read(self.open(reference), start, stop)

    def clear_cache(self) -> None:
        self.reader.clear_cache()
        self._cache.clear()

    def _open_resource(self, resource: DataResource) -> Opened:
        return self.reader._open_resource(resource)

    def _opened_revision(self, opened: Opened) -> object:
        return self.reader._opened_revision(opened)

    def _prepare_for_ui(
        self,
        opened: Opened,
        ui: BufferUI,
    ) -> Selected:
        return self._prepare(opened, ui)

    def _range(
        self,
        opened: Opened,
        start: float,
        stop: float | None,
    ) -> tuple[float, float]:
        total = self.duration(opened)
        try:
            left = float(start)
            right = min(total, left + self.default) if stop is None else float(stop)
        except (TypeError, ValueError) as error:
            raise TypeError("buffer start and stop must be numbers") from error
        if not isfinite(left) or not isfinite(right):
            raise ValueError("buffer start and stop must be finite")
        if not 0 <= left < right <= total:
            raise ValueError(
                f"buffer must satisfy 0 <= start < stop <= duration ({total:g})"
            )
        return left, right

    def _prepare(self, opened: Opened, ui: BufferUI) -> Selected:
        total = self.duration(opened)
        maximum = min(total, self.maximum(opened))
        minimum = min(maximum, self.minimum)
        default = min(maximum, max(minimum, self.default))
        width = (
            float(ui.number(
                "buffer_duration",
                label=f"Buffer duration ({self.time_unit})",
                default=default,
                minimum=minimum,
                maximum=maximum,
                step=self.buffer_step,
                group="Buffering",
            ))
            if maximum > minimum
            else default
        )
        position = float(ui.playback(
            mode=self.mode,
            duration=total,
            step=self.seek_step,
            refresh_interval=self.refresh_interval,
            loop=self.loop,
            time_unit=self.time_unit,
        ))
        start = min(max(0.0, position), max(0.0, total - width))
        stop = min(total, start + width)
        return self._cache.get(
            opened,
            self.reader._opened_revision(opened),
            start,
            stop,
            lambda: self._read(opened, start, stop),
        )


__all__ = [
    "BufferedReader",
    "Files",
    "PlaybackReader",
    "Reader",
    "SegmentedReader",
    "WindowedReader",
]
