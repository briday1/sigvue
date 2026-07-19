"""Tiny framework-only workspaces used by the test suite."""

from __future__ import annotations

import json
from pathlib import Path
from matplotlib.figure import Figure
import plotly.graph_objects as go

from sigvue.plugin import Annotation, AnnotationField, CapabilityChoice, AnalysisWorkspace, DataResource, DiscoveryColumn
from sigvue.web.application import SigvueApp


class MemorySource:
    def discover(self):
        return [DataResource(
            "recording",
            "Recording",
            source=(1.0, 2.0, 3.0, 4.0),
            summary={"date": "2026-01-02T03:04:05Z", "sample_rate": 2_000_000.0, "rf_frequency": None},
        )]

    def open(self, resource):
        return resource.source


class MemoryAnnotator:
    fields = (
        AnnotationField("comment", "Description / comment", "textarea", required=True),
    )

    def __init__(self):
        self.entries = []
        self.last_request = None

    def discover(self, source_data):
        return tuple(self.entries)

    def annotate(self, source_data, delivered_data, request):
        self.last_request = request
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


def identity_process(data, settings):
    return data


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
        process=identity_process,
        present=analyze_plotly,
        discovery_columns=(
            DiscoveryColumn("date", "Date", "datetime"),
            DiscoveryColumn("sample_rate", "Sampling rate", "si", unit="sample/s"),
            DiscoveryColumn("rf_frequency", "RF frequency", "si", unit="Hz"),
        ),
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
            process=identity_process,
            present=analyze_matplotlib,
        )
    )
    return app
