# Sigvue

Sigvue turns file-backed analysis scripts into a local browser application. A workspace package decides:

1. Which items are available.
2. How an item is opened.
3. What data is delivered for one analysis run.
4. How that data is processed and displayed.

The framework supplies the catalog, page layout, parameters, themes, refresh and playback controls, plot updates, background capability execution, and HTTP service.

## Install and run

```bash
python -m pip install sigvue
sigvue --config browser.toml
```

Open `http://127.0.0.1:8000`. The package contains no built-in workspaces; `browser.toml` chooses which independently installed or local workspace packages to load.

## The workspace-author contract

Most workspace packages implement one factory and two ordinary functions:

| Hook | Required | Responsibility |
| --- | --- | --- |
| `create_workspace(config)` | Yes | Construct and return an `AnalysisWorkspace`. |
| Source `discover()` / `open()` | Yes | List items and open the selected item. `DirectorySource` implements this for files. |
| `analyze(data, ui)` | Yes | Process the delivered data and register plots, tables, text, parameters, and layout. |
| Delivery `prepare(source_data, ui)` | No | Select or transform data before `analyze`, including buffering, seeking, live reads, or window selection. |
| `DataAnnotator` | No | Discover and persist domain-native annotations. No contract means no Annotate UI. |
| `DataExporter` | No | Advertise scope/format choices and serialize domain data. No contract means no Download UI. |
| Package entry point | Yes for profile loading | Give the factory a stable name for `browser.toml`. |

The data passed between these hooks is owned by the workspace package. The framework does not require a particular file format, array shape, reader, or analysis library.

### Typed lifecycle contracts

`DataSource` and `DataDelivery` are public, generic, runtime-checkable
interfaces. Their type parameters describe the complete data path:

```text
DataSource[SourceData]
    discover() -> Iterable[DataResource]
    open(resource) -> SourceData
                           │
                           ▼
DataDelivery[SourceData, DeliveredData]       optional
    prepare(source_data, ui) -> DeliveredData
                           │
                           ▼
analyze(delivered_data: DeliveredData, ui: AnalysisContext) -> None
```

`AnalysisWorkspace` has typed constructor overloads connecting these stages. A
type checker therefore catches a delivery that expects the wrong reader type or
an analysis function that expects something other than the delivery output.
The installed package includes a `py.typed` marker, so these checks also work
when `sigvue` is installed from a wheel.

Implementations may explicitly inherit the interfaces, which is recommended
for readability:

```python
from collections.abc import Iterable

from sigvue.plugin import AnalysisContext, DataDelivery, DataResource, DataSource


class MySource(DataSource[Recording]):
    def discover(self) -> Iterable[DataResource]:
        ...

    def open(self, resource: DataResource) -> Recording:
        ...


class WindowDelivery(DataDelivery[Recording, SampleWindow]):
    def prepare(
        self,
        recording: Recording,
        ui: AnalysisContext,
    ) -> SampleWindow:
        ...
```

Explicitly inherited methods are abstract, so an incomplete subclass cannot be
instantiated. Inheritance is not required: structurally compatible objects also
satisfy the interfaces. At runtime, `AnalysisWorkspace` validates that sources provide
`discover()` and `open()`, deliveries provide `prepare()`, analysis is callable,
discovery returns `DataResource` objects, and resource identifiers are unique.
Failures identify the missing method or invalid discovery value directly.

### Minimal file-backed workspace

```python
# src/my_workspace/workspace.py
import json
from pathlib import Path
from typing import TypedDict

import plotly.graph_objects as go

from sigvue.plugin import AnalysisContext, AnalysisWorkspace, DirectorySource


class ResultFile(TypedDict):
    values: list[float]


def load_result(path: Path) -> ResultFile:
    return json.loads(path.read_text())


def analyze(result: ResultFile, ui: AnalysisContext) -> None:
    scale = ui.number("scale", label="Scale", default=1.0, step=0.1)
    values = [scale * value for value in result["values"]]

    figure = go.Figure(go.Scatter(y=values, name="Value"))
    with ui.tab("Values"):
        ui.plot(figure, key="values")


def create_workspace(config):
    return AnalysisWorkspace(
        identifier=config["id"],
        name=config["name"],
        description="Inspect result files.",
        source=DirectorySource(
            config["data_root"],
            pattern="*.result.json",
            loader=load_result,
        ),
        analyze=analyze,
    )
```

Set `recursive=True` on `DirectorySource` to preserve nested directories in the
browser. The framework derives folder breadcrumbs from each file's path relative
to the source root; files are not flattened and directories are not presented as
fake analysis items. A custom source can provide the same behavior by setting
`DataResource(navigation_path=("campaign", "day-2"), ...)`.

Advertise the factory in the workspace package:

```toml
# pyproject.toml in the workspace package
[project.entry-points."sigvue.workspaces"]
my-analysis = "my_workspace.workspace:create_workspace"
```

Select and configure it:

```toml
# browser.toml
[browser]
title = "My Analysis Browser"
subtitle = "Explore scientific and analytical results"

[[workspaces]]
use = "my-analysis"
id = "results"
name = "Results"

[workspaces.config]
data_root = "./data"
```

`config` contains the `[workspaces.config]` values plus `id`, `name`, and `profile_dir`. Relative paths resolve from the directory containing `browser.toml`.

For an uninstalled workspace under development, add its repository path:

```toml
[[workspaces]]
use = "my-analysis"
path = "../my-workspace"
id = "results"
name = "Results"
```

The browser adds its `src` directory. Reloading the browser page reparses
`browser.toml` and applies added, removed, or reconfigured workspace entries
without restarting the server. Changed workspace modules are reloaded as part
of the same request; use `--no-reload` to disable subsequent automatic module
watching. A direct `module:factory` string is also accepted in `use`.

## Data delivery

Without a delivery object, `analyze` receives exactly what the source opened. A delivery object can prepare a different value while leaving analysis unchanged:

```python
from dataclasses import dataclass

from sigvue.plugin import AnalysisContext, DataDelivery


@dataclass(frozen=True)
class SampleWindow:
    start_seconds: float
    samples: list[complex]


class FrameDelivery(DataDelivery[Recording, SampleWindow]):
    def prepare(
        self,
        recording: Recording,
        ui: AnalysisContext,
    ) -> SampleWindow:
        frame_seconds = ui.number("frame_seconds", default=0.1, minimum=0.001)
        position = ui.playback(
            mode="seek",
            duration=max(0.0, recording.duration - frame_seconds),
            step=0.01,
        )
        return SampleWindow(position, recording.read(position, frame_seconds))


def analyze(window: SampleWindow, ui: AnalysisContext) -> None:
    ...
```

Pass it to `AnalysisWorkspace(delivery=FrameDelivery(), ...)`. The framework calls `source.open`, then `delivery.prepare`, then `analyze` for every requested state.

Available lifecycle modes are:

| Mode | Framework UI | Delivery behavior |
| --- | --- | --- |
| `static` | No timeline | Return the complete or fixed input. |
| `seek` | Play/pause, slider, editable time | Return the buffer at the requested time. |
| `live` | Seek controls plus **Live** | Return historical buffers or follow a growing source. |
| `windowed` | Movable and resizable interval, optionally over a full-record overview | Return only the selected interval. |
| `segmented` | Discrete markers with previous/next navigation | Return the selected regular or irregular segment. |

Use `ui.playback(...)` for static, seek, and live policies. In live mode, the delivery should check the currently available duration on each request.

Timeline values remain canonical seconds between the browser, delivery, annotations,
and exports, but a pipeline can choose the unit used by every framework-owned display:

```python
position = ui.playback(
    mode="seek",
    duration=3 * 86_400,
    step=60,
    time_unit="h",
)
```

Pass `time_unit=` to `ui.playback`, `ui.windowed`, or `ui.segmented`. Supported
values are `"ns"`, `"us"`, `"ms"`, `"s"`, `"min"`, `"h"`, and `"d"`.
`"auto"` chooses a sensible unit from the full duration. Editable boxes display
and accept that unit while the delivery continues receiving seconds, so changing
presentation units cannot change sample addressing or persisted annotation times.

For windowed selection, the workspace reads the returned interval and may provide a low-resolution overview statistic:

```python
start, end = ui.windowed(
    duration=recording.duration,
    default_window=0.1,
    minimum_window=0.001,
    step=0.001,
    overview=recording.summary_values(),
    overview_label="Activity",
    time_unit="ms",
)
return recording.read(start, end)
```

`overview` is optional. When supplied, it may be any finite 1D summary and does not need one value per sample. The framework distributes its values uniformly over the recording duration, so block statistics, sliding-window results, and decimated summaries all work. The framework draws and operates the range selector; tabs and exports receive only the value returned by the delivery policy.

For irregular stored results, provide explicit segment descriptors and use the returned descriptor to load the matching result:

```python
from sigvue.plugin import Segment

selected = ui.segmented(
    duration=recording.duration,
    segments=(
        Segment("event-1", 1.25, 0.08, "First event"),
        Segment("event-2", 4.90, 0.12, "Second event"),
    ),
)
return results_by_id[selected.identifier]
```

Regular segments with gaps or overlaps can instead use `ui.segmented(duration=..., segment_duration=..., stride=...)`. Segmented mode only owns selection and navigation; the delivery policy decides whether selecting a marker reads raw data, computes one interval lazily, or loads an existing post-processing result.

For non-playback refresh, call `ui.refresh(every=1.0)`. The framework prevents overlapping refresh requests and updates mounted views.

## Analysis UI

The commonly used `AnalysisContext` methods are:

| Method | Purpose |
| --- | --- |
| `ui.tab(label, columns=..., update=...)` | Add a tab and choose its layout and static/dynamic lifecycle. |
| `ui.plot(figure, key=...)` | Display a native Plotly or Matplotlib figure. |
| `ui.table(value, key=...)` | Display tabular data. |
| `ui.text(value, key=...)` | Display text or Markdown diagnostics. |
| `ui.number(...)`, `ui.select(...)`, `ui.color(...)` | Declare stored user parameters. |
| `ui.colormap(...)` | Add a compact Plotly colormap picker with low-to-high gradient previews. |
| `ui.limits(...)` | Add paired numeric boxes with a shared dual-handle limits bar. |
| `ui.parameter_group(...)` | Place parameters directly inside the current view. |
| `ui.view_switcher(...)` | Switch local views with buttons or a dropdown without creating another tab. |
| `ui.trace_style(...)` | Add a compact color, width, line-style, and marker picker. |
| `ui.stat(label, value)` | Add workflow-specific runtime or result details. |
| `ui.once(key, factory, depends_on=...)` | Cache item-level work across dynamic updates. |
| `ui.segmented(...)` | Select one regular or irregular timeline segment. |

Plotly figures remain interactive. Matplotlib figures are rendered as responsive PNG images. Tabs can mix plots, tables, and text.

Use `update="static"` for item context that should be computed once and `update="dynamic"` for data that follows delivery. A static plot factory can name parameter dependencies:

```python
with ui.tab("Reference", update="static"):
    ui.plot(
        lambda: make_reference_figure(data, threshold),
        key="reference",
        depends_on=("threshold",),
    )
```

## Optional annotation and export capabilities

Annotation and download are plugin-owned capabilities. If a workspace does not pass an
`annotator=` or `exporter=` to `AnalysisWorkspace`, the corresponding header menu is not
shown. The framework supplies typed field/choice helpers, renders the controls, and runs
exports on its background executor; the plugin decides how annotations are persisted and
how its domain data is serialized.

Implement `DataAnnotator` to discover timeline annotations and add one from the current
delivered value. Implement `DataExporter` to advertise scope and format choices and write
one result file into the supplied directory. `CapabilityChoice`, `AnnotationField`,
`AnnotationPlotBinding`, `Annotation`, `AnnotationRequest`, and `ExportRequest` are
available from `sigvue.plugin`.
This keeps formats such as SigMF annotations, MAT, JSON, or a domain-specific archive out
of the framework.

Plot-oriented plugins can attach an `AnnotationPlotBinding` to a numeric
`AnnotationField`. When the annotation menu opens, Sigvue fills that input from the
currently visible lower or upper edge of the named Plotly axis. The plugin declares the
unit transform and may add the current playback position for buffer-relative plot axes;
the resulting editable value is still persisted entirely by the plugin.

## HTTP API

The browser UI uses the same local JSON API available to integrations:

| Method and path | Result |
| --- | --- |
| `GET /health` | Service health. |
| `GET /workspaces` | Registered workspaces. |
| `GET /workspaces/{workspace_id}/items` | Discovered items. |
| `GET /workspaces/{workspace_id}/items/{item_id}` | Page definition and rendered views. Query parameters carry controls and timeline state. |
| `POST /workspaces/{workspace_id}/items/{item_id}/exports` | Start a plugin-owned background export with `scope`, `format`, and `control_values`. |
| `GET /exports/{job_id}` | Poll export status. |
| `GET /exports/{job_id}/{filename}` | Download a completed export. |
| `POST /workspaces/{workspace_id}/items/{item_id}/annotations` | Add an annotation through the plugin contract. |

## PyPI and standalone distribution

The PyPI wheel contains:

- The browser server and typed plugin contracts.
- Dependency metadata that installs Plotly and Matplotlib.
- The PyInstaller spec under `sigvue._packaging`.
- The `sigvue-build` command.

To build a platform-specific, one-file executable:

```bash
python -m pip install "sigvue[build]"
sigvue-build
```

The result is `dist/sigvue` or `dist/sigvue.exe`. Build separately on Windows, Linux, and macOS.

Workspace packages, `browser.toml`, and data remain external to the executable.

## Development

```bash
python -m pip install -e ".[build]"
PYTHONPATH=src python -m unittest discover -s tests -q
```

Neutral, runnable workspace packages are maintained separately so the framework distribution stays format-independent: [Sigvue Examples](https://github.com/briday1/sigvue-examples).
