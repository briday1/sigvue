"""Tiny framework-only workspaces used by the test suite."""

from __future__ import annotations

import json
from pathlib import Path
from matplotlib.figure import Figure
import plotly.graph_objects as go

from sigvue.plugin import Annotation, AnnotationField, CapabilityChoice, AnalysisWorkspace, DataResource
from sigvue.web.application import SigvueApp


class MemorySource:
    def discover(self):
        return [DataResource("recording", "Recording", source=(1.0, 2.0, 3.0, 4.0))]

    def open(self, resource):
        return resource.source


class MemoryAnnotator:
    fields = (
        AnnotationField("comment", "Description / comment", "textarea", required=True),
    )

    def __init__(self):
        self.entries = []

    def discover(self, source_data):
        return tuple(self.entries)

    def annotate(self, source_data, delivered_data, request):
        annotation = Annotation(
            f"annotation-{len(self.entries) + 1}",
            request.position_seconds,
            request.duration_seconds,
            None,
            request.values["comment"],
        )
        self.entries.append(annotation)
        return annotation


class MemoryExporter:
    scopes = (CapabilityChoice("buffer", "Current buffer"), CapabilityChoice("full", "Full file"))
    formats = (CapabilityChoice("json", "JSON"),)

    def export(self, source_data, delivered_data, request, directory: Path):
        target = directory / f"recording-{request.scope}.json"
        target.write_text(json.dumps({"scope": request.scope, "data": delivered_data}), encoding="utf-8")
        return target


def analyze_plotly(data, ui):
    gain = ui.number("gain", default=1.0, step=0.1)
    ui.playback(mode="seek", duration=2.0, step=0.25)
    with ui.tab("Summary", update="static"):
        ui.text("# Summary\nSynthetic test recording", key="summary")
    with ui.tab("Signal"):
        ui.plot(go.Figure(go.Scatter(y=[gain * value for value in data])), key="signal")


def analyze_matplotlib(data, ui):
    ui.playback(mode="seek", duration=2.0, step=0.25)
    figure = Figure()
    axes = figure.subplots()
    axes.plot(data)
    with ui.tab("Signal"):
        ui.plot(figure, key="signal")


def create_workspace(config=None):
    values = config or {}
    return AnalysisWorkspace(
        identifier=str(values.get("id", "test-workspace")),
        name=str(values.get("name", "Test Workspace")),
        description="Framework test fixture",
        source=MemorySource(),
        annotator=MemoryAnnotator(),
        exporter=MemoryExporter(),
        analyze=analyze_plotly,
    )


def create_test_app() -> SigvueApp:
    app = SigvueApp(title="Sigvue")
    app.register_workspace(create_workspace())
    app.register_workspace(
        AnalysisWorkspace(
            identifier="matplotlib-workspace",
            name="Matplotlib Workspace",
            description="Matplotlib export fixture",
            source=MemorySource(),
            analyze=analyze_matplotlib,
        )
    )
    return app
