"""Minimal standards-shaped SigMF fixture writing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from math import isfinite
from pathlib import Path

import numpy as np

from .recording import SIGMF_WRITABLE_DATATYPES


def _sorted_segments(
    entries: Iterable[Mapping[str, object]],
    *,
    label: str,
    sample_offset: int,
    sample_count: int,
) -> list[dict[str, object]]:
    """Validate absolute SigMF sample coordinates and sort them stably."""
    stop = sample_offset + sample_count
    result: list[dict[str, object]] = []
    for value in entries:
        if not isinstance(value, Mapping):
            raise TypeError(f"{label} entries must be mappings")
        entry = dict(value)
        start = entry.get("core:sample_start")
        if isinstance(start, bool) or not isinstance(start, int):
            raise ValueError(
                f"{label} core:sample_start values must be integers"
            )
        if (
            start < sample_offset
            or start > stop
            or (sample_count > 0 and start == stop)
        ):
            raise ValueError(
                f"{label} sample starts must lie within the recording"
            )
        if label == "Annotation":
            count = entry.get("core:sample_count")
            if count is not None and (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                raise ValueError(
                    "Annotation core:sample_count values must be "
                    "non-negative integers"
                )
            if count is not None and start + count > stop:
                raise ValueError(
                    "Annotation sample ranges must lie within the recording"
                )
        result.append(entry)
    result.sort(key=lambda entry: int(entry["core:sample_start"]))
    return result


def write_sigmf_recording(
    directory: str | Path,
    stem: str,
    samples: np.ndarray,
    sample_rate: float,
    *,
    datatype: str = "cf32_le",
    description: str | None = None,
    global_metadata: Mapping[str, object] | None = None,
    captures: Iterable[Mapping[str, object]] | None = None,
    annotations: Iterable[Mapping[str, object]] = (),
) -> tuple[Path, Path]:
    """Write channel-first complex samples and their SigMF metadata pair."""
    if datatype == "sc16_le":
        raise ValueError(
            "sc16_le is supported for legacy reads only; write ci16_le instead"
        )
    if datatype not in SIGMF_WRITABLE_DATATYPES:
        raise ValueError(f"Unsupported SigMF datatype: {datatype}")
    if not isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample_rate must be finite and positive")
    if (
        not isinstance(stem, str)
        or not stem
        or stem in {".", ".."}
        or Path(stem).name != stem
        or any(character in stem for character in "\r\n\0")
    ):
        raise ValueError("stem must be a plain filename component")
    values = np.asarray(samples)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2:
        raise ValueError("samples must have shape (samples,) or (channels, samples)")
    if values.shape[0] < 1:
        raise ValueError("samples must contain at least one channel")
    if datatype == "ci16_le" and not np.isfinite(values).all():
        raise ValueError("Integer SigMF samples must be finite")
    supplied_global = dict(global_metadata or {})
    supplied_offset = supplied_global.get("core:offset", 0)
    if (
        isinstance(supplied_offset, bool)
        or not isinstance(supplied_offset, int)
        or supplied_offset < 0
    ):
        raise ValueError("core:offset must be a non-negative sample index")
    selected_captures = _sorted_segments(
        (
            captures
            if captures is not None
            else ({"core:sample_start": supplied_offset},)
        ),
        label="Capture",
        sample_offset=supplied_offset,
        sample_count=int(values.shape[1]),
    )
    payload_annotations = _sorted_segments(
        annotations,
        label="Annotation",
        sample_offset=supplied_offset,
        sample_count=int(values.shape[1]),
    )
    standard_global: dict[str, object] = supplied_global
    standard_global.update({
        "core:datatype": datatype,
        "core:sample_rate": float(sample_rate),
        "core:num_channels": int(values.shape[0]),
    })
    standard_global.setdefault("core:version", "1.2.0")
    if description is not None:
        standard_global["core:description"] = description
    payload = {
        "global": standard_global,
        "captures": selected_captures,
        "annotations": payload_annotations,
    }
    serialized_metadata = json.dumps(
        payload,
        indent=2,
        allow_nan=False,
    ) + "\n"
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    metadata_path = root / f"{stem}.sigmf-meta"
    data_path = root / f"{stem}.sigmf-data"
    frames = values.T
    if datatype == "cf32_le":
        np.asarray(frames, dtype="<c8").tofile(data_path)
    else:
        iq = np.stack((frames.real, frames.imag), axis=-1)
        encoded = np.clip(
            np.rint(iq * 32768.0),
            -32768,
            32767,
        ).astype("<i2")
        encoded.tofile(data_path)
    metadata_path.write_text(
        serialized_metadata,
        encoding="utf-8",
    )
    return metadata_path, data_path
