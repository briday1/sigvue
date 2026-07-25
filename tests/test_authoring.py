from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import sigvue
from sigvue import DataResource, Files, Reader, Segment, Workspace
from sigvue.core.workspace import AnalysisContext


def test_public_api_does_not_expose_staged_lifecycle_types():
    assert importlib.util.find_spec("sigvue.plugin") is None
    assert not hasattr(sigvue, "Pipeline")
    for name in (
        "Analysis",
        "Delivery",
        "DirectorySource",
        "ParameterContext",
        "Presentation",
        "Source",
        "ViewContext",
    ):
        assert not hasattr(sigvue, name)


def test_files_support_an_exact_headless_window_flow(tmp_path: Path):
    first = tmp_path / "first.samples"
    second = tmp_path / "second.samples"
    first.write_text("0,1,2,3,4,5")
    second.write_text("10,11,12")

    files = Reader.files(
        tmp_path,
        "*.samples",
        lambda path: tuple(float(value) for value in path.read_text().split(",")),
    )
    reader = files.windowed(
        lambda recording, start, stop: recording[int(start) : int(stop)],
        duration=lambda recording: float(len(recording)),
        default=2.0,
        overview=lambda recording: recording,
        overview_label="Power",
        minimum=1.0,
        step=1.0,
        time_unit="samples",
    )

    assert files.discover() == (first, second)
    assert files.load(first) == (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
    assert reader.load(first, 1.0, 4.0) == (1.0, 2.0, 3.0)


def test_reader_keeps_native_references_outside_the_browser_model():
    references = ({"id": "a", "values": [1, 2]},)
    reader = Reader(
        lambda: references,
        lambda reference: tuple(reference["values"]),
        describe=lambda reference: DataResource(
            reference["id"],
            f"Item {reference['id']}",
            source=reference["id"],
        ),
    )

    assert reader.discover()[0] is references[0]
    assert reader.open(references[0]) == (1, 2)
    assert reader.resources()[0].title == "Item a"
    assert not hasattr(reader, "source")


def test_window_cache_avoids_reopening_and_rereading_for_tab_changes(
    tmp_path: Path,
):
    path = tmp_path / "samples.txt"
    path.write_text("0,1,2,3")
    calls = {"open": 0, "read": 0, "overview": 0}

    def open_recording(selected: Path):
        calls["open"] += 1
        return tuple(float(value) for value in selected.read_text().split(","))

    def read_window(recording, start, stop):
        calls["read"] += 1
        return recording[int(start) : int(stop)]

    def overview(recording):
        calls["overview"] += 1
        return recording

    reader = Files(tmp_path, "*.txt", open_recording).windowed(
        read_window,
        duration=lambda recording: len(recording),
        default=2.0,
        minimum=1.0,
        overview=overview,
    )
    resource = reader.resources()[0]
    first_open = reader._open_resource(resource)
    second_open = reader._open_resource(resource)
    assert first_open is second_open

    values = {"__window_start_seconds": 1.0, "__window_end_seconds": 3.0}
    first = reader._prepare_for_ui(first_open, AnalysisContext(values))
    second = reader._prepare_for_ui(
        second_open,
        AnalysisContext({**values, "__view_selection___tabs": 1}),
    )
    assert first == second == (1.0, 2.0)
    assert calls == {"open": 1, "read": 1, "overview": 1}

    path.write_text("0,1,2,3,4")
    changed = reader._open_resource(reader.resources()[0])
    assert changed == (0.0, 1.0, 2.0, 3.0, 4.0)
    assert calls["open"] == 2


def test_segmented_reader_passes_the_selected_segment(tmp_path: Path):
    path = tmp_path / "segments.txt"
    path.write_text("0,1,2,3,4,5")

    def select(recording, segment: Segment):
        start = int(segment.start_seconds)
        stop = start + int(segment.duration_seconds)
        return segment.identifier, recording[start:stop]

    reader = Files(
        tmp_path,
        "*.txt",
        lambda selected: tuple(
            int(value) for value in selected.read_text().split(",")
        ),
    ).segmented(
        select,
        duration=6.0,
        segments=(
            Segment("early", 0.0, 2.0, "Early"),
            Segment("late", 3.0, 3.0, "Late"),
        ),
        default="late",
        time_unit="samples",
    )

    opened = reader.open(path)
    assert reader.read(opened) == ("late", (3, 4, 5))
    assert reader.read(opened, "early") == ("early", (0, 1))
    assert reader.load(path, 1) == ("late", (3, 4, 5))


def test_playback_reader_is_headless_and_selects_a_moving_buffer(tmp_path: Path):
    path = tmp_path / "stream.txt"
    path.write_text("0,1,2,3,4,5")
    reader = Files(
        tmp_path,
        "*.txt",
        lambda selected: tuple(
            int(value) for value in selected.read_text().split(",")
        ),
    ).playback(
        lambda recording, start, stop: recording[int(start) : int(stop)],
        duration=6.0,
        default=2.0,
        minimum=1.0,
        maximum=3.0,
        buffer_step=1.0,
        mode="seek",
        seek_step=1.0,
        time_unit="samples",
    )

    assert reader.load(path, 2.0, 5.0) == (2, 3, 4)
    selected = reader._prepare_for_ui(
        reader._open_resource(reader.resources()[0]),
        AnalysisContext(
            {
                "buffer_duration": 3.0,
                "__playback_time_seconds": 2.0,
            }
        ),
    )
    assert selected == (2, 3, 4)


def test_buffered_reader_keeps_custom_selection_at_the_reader_boundary(
    tmp_path: Path,
):
    path = tmp_path / "recording.txt"
    path.write_text("0,1,2,3,4")
    files = Files(
        tmp_path,
        "*.txt",
        lambda selected: tuple(
            int(value) for value in selected.read_text().split(",")
        ),
    )

    def read(recording, start=0, count=None):
        stop = len(recording) if count is None else start + count
        return recording[start:stop]

    def select(recording, ui):
        start = int(ui.number("custom_start", default=1, minimum=0))
        count = int(ui.number("custom_count", default=2, minimum=1))
        return read(recording, start, count)

    reader = files.buffered(read, select)
    assert reader.load(path, 2, 2) == (2, 3)

    def view(data, ui):
        with ui.tab("Values"):
            ui.view(lambda: tuple(data), key="values")

    workspace = Workspace(
        identifier="custom-buffer",
        name="Custom buffer",
        description="Application-specific buffer controls",
        reader=reader,
        view=view,
    )
    opened = workspace.open_item_with_values(
        "recording.txt",
        {"custom_start": 2, "custom_count": 2},
    )
    assert opened.page.views[0].callback(
        {"custom_start": 2, "custom_count": 2}
    ) == (2, 3)


def test_workspace_has_one_unrestricted_view_callback(tmp_path: Path):
    path = tmp_path / "recording.txt"
    path.write_text("1,2,3,4")
    calls = {"read": 0, "analysis": 0, "quick": 0, "slow": 0}

    def read_window(recording, start, stop):
        calls["read"] += 1
        return recording[int(start) : int(stop)]

    reader = Files(
        tmp_path,
        "*.txt",
        lambda selected: tuple(
            int(value) for value in selected.read_text().split(",")
        ),
    ).windowed(
        read_window,
        duration=4.0,
        default=2.0,
        minimum=1.0,
    )

    def view(data, ui):
        gain = int(ui.number("gain", default=2, minimum=1, step=1))

        def analyze():
            calls["analysis"] += 1
            return tuple(value * gain for value in data)

        products = ui.compute("analysis", analyze)
        ui.stat("Buffer items", len(data))
        with ui.tab("Quick"):
            ui.view(
                lambda: calls.__setitem__("quick", calls["quick"] + 1)
                or {"values": products},
                key="quick",
            )
        with ui.tab("Slow"):
            ui.view(
                lambda: calls.__setitem__("slow", calls["slow"] + 1)
                or {"squares": tuple(value**2 for value in products)},
                key="slow",
            )

    workspace = Workspace(
        identifier="single-callback",
        name="Single callback",
        description="No process/presentation split",
        reader=reader,
        view=view,
    )

    values = {"__window_start_seconds": 0.0, "__window_end_seconds": 2.0}
    first = workspace.open_item_with_values("recording.txt", values)
    assert workspace.view is view
    assert workspace.lazy_views is True
    assert not hasattr(workspace, "analysis")
    assert not hasattr(workspace, "presentation")
    assert "Workspace callback runtime" in first.page.statistics
    assert "Presentation runtime" not in first.page.statistics
    assert first.page.statistics["Buffer items"] == 2
    assert first.page.views[0].callback(values) == {"values": (2, 4)}
    assert calls == {"read": 1, "analysis": 1, "quick": 1, "slow": 0}

    slow_values = {**values, "__view_selection___tabs": 1}
    second = workspace.open_item_with_values("recording.txt", slow_values)
    assert second.page.views[1].callback(slow_values) == {
        "squares": (4, 16)
    }
    assert calls == {"read": 1, "analysis": 1, "quick": 1, "slow": 1}

    changed = {**values, "gain": 3}
    third = workspace.open_item_with_values("recording.txt", changed)
    assert third.page.views[0].callback(changed) == {"values": (3, 6)}
    assert calls["analysis"] == 2


def test_compute_cache_uses_custom_reader_revision():
    state = {"revision": 1, "values": (1, 2)}
    calls = {"analysis": 0}

    reader = Reader(
        lambda: ("recording",),
        lambda reference: tuple(state["values"]),
        describe=lambda reference: DataResource(
            reference,
            "Recording",
            source=reference,
        ),
        revision=lambda reference: state["revision"],
    )

    def view(data, ui):
        def analyze():
            calls["analysis"] += 1
            return sum(data)

        value = ui.compute("analysis", analyze)
        with ui.tab("Value"):
            ui.view(lambda: value, key="value")

    workspace = Workspace(
        identifier="revision",
        name="Revision",
        description="Revision-aware compute cache",
        reader=reader,
        view=view,
    )

    first = workspace.open_item("recording")
    assert first.page.views[0].callback({}) == 3
    state.update(revision=2, values=(10, 20))
    second = workspace.open_item("recording")
    assert second.page.views[0].callback({}) == 30
    assert calls["analysis"] == 2


def test_static_view_cache_uses_custom_reader_revision():
    state = {"revision": 1, "values": (1, 2)}
    reader = Reader(
        lambda: ("recording",),
        lambda reference: tuple(state["values"]),
        describe=lambda reference: DataResource(
            reference,
            "Recording",
            source=reference,
        ),
        revision=lambda reference: state["revision"],
    )

    def view(data, ui):
        with ui.tab("Values", update="static"):
            ui.view(lambda: tuple(data), key="values")

    workspace = Workspace(
        identifier="static-revision",
        name="Static revision",
        description="Revision-aware static views",
        reader=reader,
        view=view,
    )
    first = workspace.open_item("recording")
    assert first.page.views[0].callback({}) == (1, 2)
    state.update(revision=2, values=(10, 20))
    second = workspace.open_item("recording")
    assert second.page.views[0].callback({}) == (10, 20)


def test_workspace_requires_one_reader_and_one_view_callback():
    reader = Reader(lambda: (), lambda reference: reference)
    with pytest.raises(TypeError, match="required keyword-only argument: 'view'"):
        Workspace(
            identifier="missing-view",
            name="Missing",
            description="Missing",
            reader=reader,
        )
    with pytest.raises(TypeError, match="unexpected keyword argument 'presentation'"):
        Workspace(
            identifier="mixed",
            name="Mixed",
            description="Mixed",
            reader=reader,
            view=lambda data, ui: None,
            presentation=object(),
        )
