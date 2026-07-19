"""Stable public API for typed, data-driven workspace plugins."""

from sigvue.core.plugin import (
    AnalysisWorkspace,
    DataDelivery,
    DeliveryContext,
    DiscoveryColumn,
    DataResource,
    DataSource,
    DirectorySource,
    ParameterContext,
    TraceStyle,
    ViewContext,
)
from sigvue.core.page import AxisNavigation, PlaybackMode, Segment, TimeUnit
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
    "AnalysisWorkspace",
    "AxisNavigation",
    "Annotation",
    "AnnotationField",
    "AnnotationPlotBinding",
    "AnnotationRequest",
    "CapabilityChoice",
    "DataDelivery",
    "DeliveryContext",
    "DiscoveryColumn",
    "DataAnnotator",
    "DataExporter",
    "DataResource",
    "DataSource",
    "DirectorySource",
    "ExportRequest",
    "PlaybackMode",
    "ParameterContext",
    "Segment",
    "TraceStyle",
    "ViewContext",
    "TimeUnit",
]
