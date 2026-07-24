"""Reusable JSON/MAT complex-sample exporters."""

from __future__ import annotations

from contextlib import contextmanager
import json
from math import isfinite
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

import numpy as np
from scipy.io import savemat

from sigvue.plugin import CapabilityChoice, Exporter, ExportRequest

from .recording import SigMFRecording, load_metadata


SAMPLE_EXPORT_SCOPES = (
    CapabilityChoice("buffer", "Current buffer"),
    CapabilityChoice("full", "Full file"),
)
SAMPLE_EXPORT_FORMATS = (
    CapabilityChoice("json", "JSON"),
    CapabilityChoice("mat", "MAT"),
)
_MAT_METADATA_NAMES = {
    "sample_rate",
    "start_sample",
    "sample_count",
    "scope",
    "metadata_json",
    "control_values_json",
}


@contextmanager
def _atomic_output(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".part",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        yield temporary
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_export_coordinates(
    sample_rate: float,
    start_sample: int,
    sample_count: int,
) -> None:
    if (
        isinstance(start_sample, bool)
        or not isinstance(start_sample, int)
        or isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
    ):
        raise TypeError("Export sample coordinates must be integers")
    if not isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("Export sample_rate must be finite and positive")
    if start_sample < 0 or sample_count < 0:
        raise ValueError("Export sample coordinates cannot be negative")


def _require_finite_json_samples(
    arrays: Mapping[str, np.ndarray],
    *,
    chunk_size: int = 1_000_000,
) -> None:
    for name, values in arrays.items():
        flattened = np.asarray(values).reshape(-1)
        for start in range(0, flattened.size, chunk_size):
            if not np.isfinite(flattened[start : start + chunk_size]).all():
                raise ValueError(
                    f"JSON export cannot represent non-finite samples in {name}"
                )


def _plain_component(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
        or any(character in value for character in "\r\n\0")
    ):
        raise ValueError(f"Export {label} must be a plain filename component")


def _filename(
    stem: str,
    start: int,
    count: int,
    rate: float,
    scope: str,
    extension: str,
) -> str:
    return (
        f"{stem}-t{start / rate:.9f}s-"
        f"{count / rate:.9f}s-{scope}.{extension}"
    )


def _write_numeric_matrix(
    stream: Any,
    values: np.ndarray,
    chunk_size: int = 16_384,
) -> None:
    matrix = np.atleast_2d(values)
    stream.write("[")
    for channel_index, channel in enumerate(matrix):
        if channel_index:
            stream.write(",")
        stream.write("[")
        for start in range(0, channel.size, chunk_size):
            if start:
                stream.write(",")
            stream.write(
                ",".join(
                    repr(float(value))
                    for value in channel[start : start + chunk_size]
                )
            )
        stream.write("]")
    stream.write("]")


def _write_complex_value(stream: Any, samples: np.ndarray) -> None:
    stream.write('{"real": ')
    _write_numeric_matrix(stream, np.asarray(samples).real)
    stream.write(', "imag": ')
    _write_numeric_matrix(stream, np.asarray(samples).imag)
    stream.write("}")


def write_array_bundle_export(
    directory: str | Path,
    *,
    stem: str,
    arrays: Mapping[str, np.ndarray],
    sample_rate: float,
    start_sample: int,
    scope: str,
    format: str,
    metadata: Mapping[str, object] | None = None,
    control_values: Mapping[str, object] | None = None,
    sample_count: int | None = None,
) -> Path:
    """Write a named bundle of complex arrays without duplicating serializers."""
    normalized: dict[str, np.ndarray] = {}
    for name, samples in arrays.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Export array names must be non-empty strings")
        values = np.asarray(samples)
        if values.ndim not in {1, 2}:
            raise ValueError(
                f"Export array {name} must be one- or two-dimensional"
            )
        normalized[name] = np.atleast_2d(values)
    if not normalized:
        raise ValueError("At least one sample array is required")
    count = (
        int(sample_count)
        if sample_count is not None
        else max(samples.shape[-1] for samples in normalized.values())
    )
    _validate_export_coordinates(sample_rate, start_sample, count)
    _plain_component(stem, "stem")
    _plain_component(scope, "scope")
    if format not in {"json", "mat"}:
        raise ValueError(f"Unsupported sample export format: {format}")
    target = Path(directory) / _filename(
        stem,
        start_sample,
        count,
        sample_rate,
        scope,
        format,
    )
    common = {
        "sample_rate": sample_rate,
        "start_sample": start_sample,
        "sample_count": count,
        "scope": scope,
        "metadata": dict(metadata or {}),
        "control_values": dict(control_values or {}),
    }
    if format == "mat":
        invalid_names = [
            name
            for name in normalized
            if re.fullmatch(r"[A-Za-z]\w{0,62}", name) is None
        ]
        if invalid_names:
            raise ValueError(
                "MAT export array names must be valid MATLAB identifiers: "
                + ", ".join(invalid_names)
            )
        collisions = sorted(_MAT_METADATA_NAMES.intersection(normalized))
        if collisions:
            raise ValueError(
                "MAT export array names conflict with export metadata: "
                + ", ".join(collisions)
            )
        payload = {
            "sample_rate": sample_rate,
            "start_sample": start_sample,
            "sample_count": count,
            "scope": scope,
            "metadata_json": json.dumps(
                metadata or {},
                default=str,
                allow_nan=False,
            ),
            "control_values_json": json.dumps(
                control_values or {},
                default=str,
                allow_nan=False,
            ),
            **normalized,
        }
        with _atomic_output(target) as temporary:
            savemat(
                temporary,
                payload,
                appendmat=False,
            )
        return target
    _require_finite_json_samples(normalized)
    common_json = json.dumps(
        common,
        default=str,
        allow_nan=False,
    )
    with _atomic_output(target) as temporary, temporary.open(
        "w",
        encoding="utf-8",
    ) as stream:
        stream.write(common_json[:-1])
        stream.write(', "samples": {')
        for index, (name, samples) in enumerate(normalized.items()):
            if index:
                stream.write(",")
            stream.write(f"{json.dumps(name)}: ")
            _write_complex_value(stream, samples)
        stream.write("}}")
    return target


def write_sample_export(
    directory: str | Path,
    *,
    stem: str,
    samples: np.ndarray,
    sample_rate: float,
    start_sample: int,
    scope: str,
    format: str,
    metadata: Mapping[str, object],
    control_values: Mapping[str, object] | None = None,
) -> Path:
    """Write one complex sample matrix with a compact JSON or MAT schema."""
    values = np.asarray(samples)
    if values.ndim not in {1, 2}:
        raise ValueError("Export samples must be one- or two-dimensional")
    normalized = np.atleast_2d(values)
    if format == "mat":
        return write_array_bundle_export(
            directory,
            stem=stem,
            arrays={"samples": normalized},
            sample_rate=sample_rate,
            start_sample=start_sample,
            scope=scope,
            format=format,
            metadata=metadata,
            control_values=control_values,
            sample_count=normalized.shape[-1],
        )
    if format != "json":
        raise ValueError(f"Unsupported sample export format: {format}")
    _validate_export_coordinates(
        sample_rate,
        start_sample,
        normalized.shape[-1],
    )
    _plain_component(stem, "stem")
    _plain_component(scope, "scope")
    _require_finite_json_samples({"samples": normalized})
    target = Path(directory) / _filename(
        stem,
        start_sample,
        normalized.shape[-1],
        sample_rate,
        scope,
        format,
    )
    common = {
        "sample_rate": sample_rate,
        "start_sample": start_sample,
        "sample_count": normalized.shape[-1],
        "channel_count": normalized.shape[0],
        "scope": scope,
        "metadata": dict(metadata),
        "control_values": dict(control_values or {}),
    }
    common_json = json.dumps(
        common,
        default=str,
        allow_nan=False,
    )
    with _atomic_output(target) as temporary, temporary.open(
        "w",
        encoding="utf-8",
    ) as stream:
        stream.write(common_json[:-1])
        stream.write(', "samples": ')
        _write_complex_value(stream, normalized)
        stream.write("}")
    return target


class SigMFExporter(Exporter[SigMFRecording, Any]):
    """Drop-in current-buffer/full-recording JSON and MAT export."""

    @property
    def scopes(self) -> tuple[CapabilityChoice, ...]:
        return SAMPLE_EXPORT_SCOPES

    @property
    def formats(self) -> tuple[CapabilityChoice, ...]:
        return SAMPLE_EXPORT_FORMATS

    def export(
        self,
        recording: SigMFRecording,
        delivered: Any,
        request: ExportRequest,
        directory: Path,
    ) -> Path:
        if request.scope not in {"buffer", "full"}:
            raise ValueError(f"Unsupported sample export scope: {request.scope}")
        if request.format not in {"json", "mat"}:
            raise ValueError(
                f"Unsupported sample export format: {request.format}"
            )
        if request.scope == "full" or delivered is recording:
            start, samples = 0, recording.read(0, recording.sample_count)
        else:
            start = int(getattr(delivered, "start_sample"))
            samples = np.asarray(getattr(delivered, "samples"))
        return write_sample_export(
            directory,
            stem=recording.metadata_path.name.removesuffix(".sigmf-meta"),
            samples=samples,
            sample_rate=recording.sample_rate,
            start_sample=start,
            scope=request.scope,
            format=request.format,
            metadata=load_metadata(recording.metadata_path),
            control_values=request.control_values,
        )
