"""Framework-independent SigMF metadata and ranged sample I/O."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
from threading import RLock

import numpy as np


SIGMF_DATATYPES: dict[str, tuple[str, int, float]] = {
    "cf32_le": ("<f4", 4, 1.0),
    "ci16_le": ("<i2", 2, 1.0 / 32768.0),
    "sc16_le": ("<i2", 2, 1.0 / 32768.0),
}
SIGMF_WRITABLE_DATATYPES = ("cf32_le", "ci16_le")
_metadata_lock = RLock()


@dataclass(frozen=True)
class SigMFRecording:
    """A validated SigMF recording with channel-first ranged reads."""

    metadata_path: Path
    data_path: Path
    sample_rate: float
    channel_count: int
    sample_count: int
    metadata: dict[str, object]
    datatype: str
    sample_offset: int = 0

    @property
    def duration_seconds(self) -> float:
        return self.sample_count / self.sample_rate

    @property
    def center_frequency(self) -> float:
        return self.center_frequency_at(0)

    def center_frequency_at(self, sample: int) -> float:
        """Return the active capture's tuning at one local sample index.

        ``core:offset`` is a logical sample origin, not a byte offset. Public
        recording APIs use local indices, while capture metadata uses absolute
        SigMF sample indices.
        """
        if isinstance(sample, bool) or not isinstance(sample, int):
            raise TypeError("sample must be an integer")
        if not 0 <= sample <= self.sample_count:
            raise ValueError("sample is outside the recording")
        absolute_sample = self.sample_offset + sample
        captures = self.metadata.get("captures", [])
        if not isinstance(captures, list):
            raise ValueError("SigMF captures must be an array")
        eligible: list[tuple[int, dict[str, object]]] = []
        for capture in captures:
            if not isinstance(capture, dict):
                raise ValueError("SigMF captures must be objects")
            raw_start = capture.get("core:sample_start", self.sample_offset)
            if isinstance(raw_start, bool) or not isinstance(raw_start, int):
                raise ValueError("Capture sample starts must be integers")
            if raw_start <= absolute_sample:
                eligible.append((raw_start, capture))
        if not eligible:
            return 0.0
        _, selected = max(eligible, key=lambda item: item[0])
        if selected.get("core:frequency") is None:
            return 0.0
        frequency = float(selected["core:frequency"])
        if not isfinite(frequency):
            raise ValueError("Capture center frequencies must be finite")
        return frequency

    def read(
        self,
        start: int,
        count: int,
        *,
        normalized: bool = True,
    ) -> np.ndarray:
        """Read requested frames as channel-first complex64.

        Integer recordings are normalized to approximately ``[-1, 1]`` by
        default. Set ``normalized=False`` when domain calibration requires
        native ADC counts.
        """
        start = min(self.sample_count, max(0, int(start)))
        count = min(max(0, int(count)), self.sample_count - start)
        scalar_type, scalar_bytes, scale = SIGMF_DATATYPES[self.datatype]
        scalars_per_frame = self.channel_count * 2
        with self.data_path.open("rb") as stream:
            stream.seek(start * scalars_per_frame * scalar_bytes)
            scalars = np.fromfile(
                stream,
                dtype=scalar_type,
                count=count * scalars_per_frame,
            )
        if scalars.size != count * scalars_per_frame:
            raise ValueError(f"{self.data_path.name} ended during a ranged read")
        frames = scalars.reshape(-1, self.channel_count, 2)
        applied_scale = scale if normalized else 1.0
        complex_frames = (
            frames[..., 0] + 1j * frames[..., 1]
        ) * applied_scale
        return np.asarray(complex_frames, dtype=np.complex64).T


def load_metadata(metadata_path: str | Path) -> dict[str, object]:
    """Read one SigMF metadata document without workspace semantics."""
    payload = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SigMF metadata must be a JSON object")
    return payload


def annotations(metadata_path: str | Path) -> tuple[dict[str, object], ...]:
    """Read the current on-disk SigMF annotations."""
    return tuple(load_metadata(metadata_path).get("annotations", ()))


def append_annotation(metadata_path: str | Path, annotation: dict[str, object]) -> None:
    """Atomically append and sample-sort a SigMF annotation object."""
    path = Path(metadata_path)
    with _metadata_lock:
        metadata = load_metadata(path)
        entries = list(metadata.get("annotations", ()))
        entries.append(dict(annotation))
        entries.sort(key=lambda entry: int(entry["core:sample_start"]))
        metadata["annotations"] = entries
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(metadata, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def load_sigmf_recording(
    metadata_path: str | Path,
    *,
    sample_rate_fallback: float | None = None,
) -> SigMFRecording:
    """Validate and open common little-endian complex SigMF datatypes."""
    path = Path(metadata_path)
    metadata = load_metadata(path)
    global_metadata = metadata.get("global")
    if not isinstance(global_metadata, dict):
        raise ValueError(f"{path.name} must define a global metadata object")
    datatype = str(global_metadata.get("core:datatype"))
    if datatype not in SIGMF_DATATYPES:
        raise ValueError(f"Unsupported SigMF datatype: {datatype}")
    channel_count = int(global_metadata.get("core:num_channels") or 1)
    if channel_count < 1:
        raise ValueError(f"{path.name} must define at least one channel")
    raw_sample_rate = global_metadata.get("core:sample_rate")
    if raw_sample_rate is None and sample_rate_fallback is None:
        raise ValueError(f"{path.name} does not define core:sample_rate")
    sample_rate = float(
        raw_sample_rate if raw_sample_rate is not None else sample_rate_fallback
    )
    if not isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError(
            f"{path.name} must have a finite, positive sample rate"
        )
    raw_offset = global_metadata.get("core:offset", 0)
    if isinstance(raw_offset, bool) or not isinstance(raw_offset, int):
        raise ValueError(f"{path.name} core:offset must be an integer")
    if raw_offset < 0:
        raise ValueError(f"{path.name} core:offset cannot be negative")
    data_path = path.with_name(
        path.name.removesuffix(".sigmf-meta") + ".sigmf-data"
    )
    if not data_path.is_file():
        raise ValueError(f"Missing SigMF sample data: {data_path.name}")
    _, scalar_bytes, _ = SIGMF_DATATYPES[datatype]
    frame_bytes = channel_count * 2 * scalar_bytes
    sample_bytes = data_path.stat().st_size
    if sample_bytes % frame_bytes:
        raise ValueError(f"{data_path.name} contains a partial sample frame")
    return SigMFRecording(
        path,
        data_path,
        sample_rate,
        channel_count,
        sample_bytes // frame_bytes,
        metadata,
        datatype,
        raw_offset,
    )
