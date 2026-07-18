"""Stable public API for typed, data-driven workspace plugins."""

from sigvue.core.plugin import (
    AnalysisContext,
    AnalysisWorkspace,
    DataDelivery,
    DataResource,
    DataSource,
    DirectorySource,
    TraceStyle,
)
from sigvue.core.page import PlaybackMode, Segment, TimeUnit
from sigvue.core.capabilities import (
    Annotation,
    AnnotationField,
    AnnotationPlotBinding,
    AnnotationRequest,
    CapabilityChoice,
    DataAnnotator,
    DataExporter,
    ExportRequest,
)

__all__ = [
    "AnalysisContext",
    "AnalysisWorkspace",
    "Annotation",
    "AnnotationField",
    "AnnotationPlotBinding",
    "AnnotationRequest",
    "CapabilityChoice",
    "DataDelivery",
    "DataAnnotator",
    "DataExporter",
    "DataResource",
    "DataSource",
    "DirectorySource",
    "ExportRequest",
    "PlaybackMode",
    "Segment",
    "TraceStyle",
    "TimeUnit",
]
