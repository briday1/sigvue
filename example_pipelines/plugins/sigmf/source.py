"""Drop-in discovery and loading for directories of SigMF recordings."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from ..discovery import SIGNAL_DISCOVERY_COLUMNS
from sigvue.plugin import DataResource, DirectorySource

from .recording import SigMFRecording, load_metadata, load_sigmf_recording


SIGMF_DISCOVERY_COLUMNS = SIGNAL_DISCOVERY_COLUMNS


def sigmf_discovery_summary(
    metadata: dict[str, object],
) -> dict[str, object | None]:
    """Extract standard sortable browser values without inventing metadata."""
    global_metadata = metadata.get("global", {})
    captures = metadata.get("captures", [])
    capture = captures[0] if captures else {}
    return {
        "date": capture.get("core:datetime")
        or global_metadata.get("core:datetime"),
        "sample_rate": global_metadata.get("core:sample_rate"),
        "rf_frequency": capture.get("core:frequency"),
    }


def describe_sigmf_recording(
    path: Path,
    *,
    tags: Iterable[str] = ("sigmf",),
) -> DataResource:
    """Build a useful catalog item from standard SigMF metadata."""
    metadata = load_metadata(path)
    global_metadata = metadata["global"]
    sample_rate = global_metadata.get("core:sample_rate")
    coordinate_label = (
        f"{float(sample_rate) / 1e6:g} MS/s"
        if sample_rate is not None
        else "Sample-normalized (rate unavailable)"
    )
    return DataResource(
        identifier=path.name.removesuffix(".sigmf-meta"),
        title=str(global_metadata.get("core:description") or path.stem),
        source=path,
        subtitle=(
            f"{coordinate_label} · "
            f"{global_metadata.get('core:datatype', 'SigMF')}"
        ),
        timestamp=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        tags=tuple(tags),
        summary=sigmf_discovery_summary(metadata),
    )


def sigmf_source(
    directory: str | Path,
    *,
    pattern: str | tuple[str, ...] = "*.sigmf-meta",
    recursive: bool = True,
    sample_rate_fallback: float | None = None,
    describe: Callable[[Path], DataResource] | None = None,
    tags: Iterable[str] = ("sigmf",),
) -> DirectorySource[SigMFRecording]:
    """Create a complete SigMF ``Source`` with one function call."""
    root = Path(directory).expanduser().resolve()
    if describe is None:
        base_descriptor = partial(
            describe_sigmf_recording,
            tags=tuple(tags),
        )

        def descriptor(path: Path) -> DataResource:
            resource = base_descriptor(path)
            relative = path.resolve().relative_to(root)
            parent = relative.parent
            identifier = relative.as_posix().removesuffix(".sigmf-meta")
            return replace(
                resource,
                identifier=identifier.replace("/", "::"),
                navigation_path=(
                    () if parent == Path(".") else parent.parts
                ),
            )
    else:
        descriptor = describe
    loader = partial(
        load_sigmf_recording,
        sample_rate_fallback=sample_rate_fallback,
    )
    return DirectorySource(
        root,
        pattern=pattern,
        loader=loader,
        describe=descriptor,
        recursive=recursive,
    )
