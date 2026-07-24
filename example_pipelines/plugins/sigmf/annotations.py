"""Standard SigMF persistence adapters for Sigvue annotations."""

from __future__ import annotations

from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from sigvue.plugin import (
    Annotation,
    AnnotationField,
    AnnotationPlotBinding,
    AnnotationRequest,
    Annotator,
)

from .recording import SigMFRecording, annotations, append_annotation


def annotation_fields() -> tuple[AnnotationField, ...]:
    return (
        AnnotationField(
            "comment",
            "Description / comment",
            "textarea",
            required=True,
        ),
    )


@lru_cache(maxsize=64)
def _read_annotations_cached(
    metadata_path: str,
    modified_ns: int,
    size: int,
    sample_rate: float,
    sample_offset: int,
) -> tuple[Annotation, ...]:
    del modified_ns, size
    result = []
    for index, entry in enumerate(annotations(Path(metadata_path))):
        raw_start = entry["core:sample_start"]
        if isinstance(raw_start, bool) or not isinstance(raw_start, int):
            raise ValueError("Annotation sample starts must be integers")
        start = raw_start - sample_offset
        if start < 0:
            raise ValueError(
                "Annotation sample starts cannot precede the recording offset"
            )
        raw_count = entry.get("core:sample_count")
        if raw_count is not None and (
            isinstance(raw_count, bool)
            or not isinstance(raw_count, int)
            or raw_count < 0
        ):
            raise ValueError(
                "Annotation sample counts must be non-negative integers"
            )
        result.append(
            Annotation(
                identifier=str(
                    entry.get("core:uuid") or f"{raw_start}:{index}"
                ),
                start_seconds=start / sample_rate,
                duration_seconds=(
                    None if raw_count is None else raw_count / sample_rate
                ),
                label=(
                    str(entry["core:label"])
                    if entry.get("core:label")
                    else None
                ),
                comment=str(entry.get("core:comment") or "") or None,
                frequency_lower_hz=(
                    float(entry["core:freq_lower_edge"])
                    if entry.get("core:freq_lower_edge") is not None
                    else None
                ),
                frequency_upper_hz=(
                    float(entry["core:freq_upper_edge"])
                    if entry.get("core:freq_upper_edge") is not None
                    else None
                ),
            )
        )
    return tuple(result)


def read_sigmf_annotations(
    recording: SigMFRecording,
) -> tuple[Annotation, ...]:
    """Read annotations with a metadata modification-aware cache."""
    stat = recording.metadata_path.stat()
    return _read_annotations_cached(
        str(recording.metadata_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        recording.sample_rate,
        recording.sample_offset,
    )


def add_sigmf_annotation(
    recording: SigMFRecording,
    start_sample: int,
    sample_count: int,
    request: AnnotationRequest,
    *,
    identifier: str | None = None,
    frequency_lower_hz: float | None = None,
    frequency_upper_hz: float | None = None,
    generator: str = "Sigvue",
) -> Annotation:
    """Persist one time or time-frequency annotation in standard SigMF fields."""
    if (
        isinstance(start_sample, bool)
        or not isinstance(start_sample, int)
        or isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
    ):
        raise TypeError("Annotation sample coordinates must be integers")
    if (
        start_sample < 0
        or sample_count < 0
        or start_sample + sample_count > recording.sample_count
    ):
        raise ValueError(
            "Annotation sample coordinates must stay within the recording"
        )
    annotation_id = identifier or str(uuid4())
    comment = request.values.get("comment", "").strip()
    if not comment:
        raise ValueError("An annotation description/comment is required")
    entry: dict[str, object] = {
        "core:sample_start": recording.sample_offset + start_sample,
        "core:sample_count": sample_count,
        "core:comment": comment,
        "core:generator": generator,
        "core:uuid": annotation_id,
    }
    if (frequency_lower_hz is None) != (frequency_upper_hz is None):
        raise ValueError("Both lower and upper annotation frequencies are required")
    if frequency_lower_hz is not None and frequency_upper_hz is not None:
        if (
            not isfinite(frequency_lower_hz)
            or not isfinite(frequency_upper_hz)
            or frequency_lower_hz >= frequency_upper_hz
        ):
            raise ValueError(
                "Annotation frequencies must be finite and increasing"
            )
        entry["core:freq_lower_edge"] = float(frequency_lower_hz)
        entry["core:freq_upper_edge"] = float(frequency_upper_hz)
    created = Annotation(
        annotation_id,
        start_sample / recording.sample_rate,
        sample_count / recording.sample_rate,
        None,
        comment,
        frequency_lower_hz,
        frequency_upper_hz,
    )
    append_annotation(recording.metadata_path, entry)
    return created


def waterfall_annotation_fields(
    view: str,
    *,
    time_axis: str = "yaxis2",
    frequency_axis: str = "xaxis2",
    time_scale: float = 1e-3,
    frequency_scale: float = 1e6,
    time_offset_source: str = "none",
) -> tuple[AnnotationField, ...]:
    """Create box-selectable time/frequency fields for one waterfall view."""
    return (
        AnnotationField(
            "start_seconds",
            "Recording start (s)",
            "number",
            required=True,
            plot_binding=AnnotationPlotBinding(
                view,
                time_axis,
                "lower",
                scale=time_scale,
                offset_source=time_offset_source,
                selection_policy="box_preferred",
            ),
        ),
        AnnotationField(
            "stop_seconds",
            "Recording stop (s)",
            "number",
            required=True,
            plot_binding=AnnotationPlotBinding(
                view,
                time_axis,
                "upper",
                scale=time_scale,
                offset_source=time_offset_source,
                selection_policy="box_preferred",
            ),
        ),
        AnnotationField(
            "frequency_lower_hz",
            "Lower RF frequency (Hz)",
            "number",
            required=True,
            plot_binding=AnnotationPlotBinding(
                view,
                frequency_axis,
                "lower",
                scale=frequency_scale,
                selection_policy="box_preferred",
            ),
        ),
        AnnotationField(
            "frequency_upper_hz",
            "Upper RF frequency (Hz)",
            "number",
            required=True,
            plot_binding=AnnotationPlotBinding(
                view,
                frequency_axis,
                "upper",
                scale=frequency_scale,
                selection_policy="box_preferred",
            ),
        ),
        *annotation_fields(),
    )


class SigMFAnnotator(Annotator[SigMFRecording, Any]):
    """Drop-in time annotation persistence for delivered sample windows."""

    def __init__(self, *, generator: str = "Sigvue") -> None:
        self.generator = generator

    @property
    def fields(self) -> tuple[AnnotationField, ...]:
        return annotation_fields()

    def discover(
        self,
        recording: SigMFRecording,
    ) -> tuple[Annotation, ...]:
        return read_sigmf_annotations(recording)

    def annotate(
        self,
        recording: SigMFRecording,
        delivered: Any,
        request: AnnotationRequest,
    ) -> Annotation:
        start = int(
            getattr(
                delivered,
                "start_sample",
                round(request.position_seconds * recording.sample_rate),
            )
        )
        samples = np.asarray(
            getattr(
                delivered,
                "samples",
                np.empty((recording.channel_count, 0)),
            )
        )
        count = int(samples.shape[-1]) if samples.ndim else 0
        return add_sigmf_annotation(
            recording,
            start,
            count,
            request,
            generator=self.generator,
        )


class WaterfallSigMFAnnotator(SigMFAnnotator):
    """Drop-in time/frequency annotation persistence for a waterfall plot."""

    def __init__(
        self,
        view: str,
        timeline_color_control: str,
        *,
        time_axis: str = "yaxis2",
        frequency_axis: str = "xaxis2",
        time_scale: float = 1e-3,
        frequency_scale: float = 1e6,
        time_offset_source: str = "none",
        generator: str = "Sigvue",
    ) -> None:
        super().__init__(generator=generator)
        self.view = view
        self.timeline_color_control = timeline_color_control
        self.time_axis = time_axis
        self.frequency_axis = frequency_axis
        self.time_scale = time_scale
        self.frequency_scale = frequency_scale
        self.time_offset_source = time_offset_source

    @property
    def fields(self) -> tuple[AnnotationField, ...]:
        return waterfall_annotation_fields(
            self.view,
            time_axis=self.time_axis,
            frequency_axis=self.frequency_axis,
            time_scale=self.time_scale,
            frequency_scale=self.frequency_scale,
            time_offset_source=self.time_offset_source,
        )

    def annotate(
        self,
        recording: SigMFRecording,
        delivered: Any,
        request: AnnotationRequest,
    ) -> Annotation:
        try:
            start_seconds = float(request.values["start_seconds"])
            stop_seconds = float(request.values["stop_seconds"])
            frequency_lower_hz = float(
                request.values["frequency_lower_hz"]
            )
            frequency_upper_hz = float(
                request.values["frequency_upper_hz"]
            )
        except (KeyError, ValueError) as error:
            raise ValueError(
                "Waterfall annotation bounds must be numeric"
            ) from error
        if (
            not all(isfinite(value) for value in (
                start_seconds,
                stop_seconds,
                frequency_lower_hz,
                frequency_upper_hz,
            ))
            or start_seconds < 0
            or stop_seconds <= start_seconds
        ):
            raise ValueError(
                "Annotation bounds must be finite, with stop time after start time"
            )
        start_sample = min(
            recording.sample_count,
            round(start_seconds * recording.sample_rate),
        )
        stop_sample = min(
            recording.sample_count,
            round(stop_seconds * recording.sample_rate),
        )
        if stop_sample <= start_sample:
            raise ValueError(
                "Annotation time bounds do not contain any recording samples"
            )
        return add_sigmf_annotation(
            recording,
            start_sample,
            stop_sample - start_sample,
            request,
            frequency_lower_hz=frequency_lower_hz,
            frequency_upper_hz=frequency_upper_hz,
            generator=self.generator,
        )
