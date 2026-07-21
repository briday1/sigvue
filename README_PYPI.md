<!-- Generated from README.md by scripts/build_pypi_readme.py. Do not edit directly. -->

# Sigvue

Sigvue turns file-backed analysis scripts into a local browser application. A
workspace package decides:

1. Which items are available.
2. How an item is opened.
3. Which parameters configure processing, if any.
4. How delivered data becomes analysis products.
5. How those products are arranged and displayed.

The framework supplies the catalog, page layout, parameters, themes, refresh
and playback controls, plot updates, background capability execution, and HTTP
service.

## Mental model

A workspace is an adapter between domain code and the Sigvue runtime. Plugin
code owns data semantics; the framework owns application lifecycle and UI
state.

![Mental model diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.24/docs/pypi-diagrams/01-mental-model.svg)

The same factory may appear multiple times in `browser.toml`. Each entry creates
a separate workspace instance with its own identity, tags, and data
configuration while reusing the same source, delivery, processing, and
presentation code.

## Install and run

```bash
python -m pip install sigvue
sigvue --config browser.toml
```

Open `http://127.0.0.1:8000`. The package contains no built-in workspaces; `browser.toml` chooses which independently installed or local workspace packages to load.

### Runnable pipeline example

This repository includes external-style reference implementations under
[`example_pipelines/`](example_pipelines/README.md). They are intentionally outside
`src/sigvue` and separate shared SigMF I/O, styling, delivery, analysis,
Plotly presentation, and workspace assembly. Generate all of the small synthetic
LTE-like and digital-modulation recordings, then launch them with:

```bash
python example_pipelines/scripts/generate_all.py
sigvue --config example_pipelines/browser.toml
```

The generated SigMF data is ignored by Git. The example exists to show a complete,
copyable plugin repository shape; it is not built into the Sigvue package.

## The workspace-author contract

Every workspace package defines one factory and composes framework objects.
`Source` owns discovery and opening, optional
`Delivery` owns request-dependent selection, `Analysis` owns configuration and
processing, and `Presentation` owns display. Annotation and export are
independent optional capabilities.
Import public plugin types from `sigvue.plugin`; `sigvue.core` is framework
implementation detail.

Only descriptive values such as identifiers, labels, tags, versions, and
column definitions are plain data. Every behavioral constructor field requires
an explicit framework object:

```python
Workspace(
    source=RecordingSource(...),
    delivery=WindowDelivery(),          # optional
    analysis=RecordingAnalysis(),
    presentation=RecordingPresentation(),
    annotator=RecordingAnnotator(),     # optional
    exporter=RecordingExporter(),       # optional
)
```

A pipeline may use a helper such as `recording_source(root)` to construct and
return one of these objects. The helper is not itself a lifecycle hook; the
value returned to `Workspace` must still inherit the corresponding framework
base class. Structural lookalikes are rejected.

### What `create_workspace()` constructs

`create_workspace(config)` must return one `Workspace`. These are the
values passed to its constructor:

| Constructor value | Required | Created by | Used for |
| --- | --- | --- | --- |
| `identifier`, `name`, `description` | Yes | Plugin defaults; profile may override | Standalone identity and fallback catalog metadata. |
| `source: Source[SourceData]` | Yes | Plugin | Discover `DataResource` records and open one domain value. |
| `analysis: Analysis[...]` | Yes | Plugin | A pipeline-specific object implementing `process()` and optionally overriding `configure()`. |
| `presentation: Presentation[...]` | Yes | Plugin | A pipeline-specific object implementing `present()` for display controls, views, statistics, and layout. |
| `delivery: Delivery[SourceData, DeliveredData]` | No | Plugin | Select a buffer, choose a segment, follow live data, or transform the opened value. |
| `annotator: Annotator[...]` | No | Plugin | Discover and persist domain-native annotations. Enables **Annotate**. |
| `exporter: Exporter[...]` | No | Plugin | Advertise formats/scopes and serialize domain data. Enables **Download**. |
| `discovery_columns` | No | Plugin | Define sortable metadata columns populated by `DataResource.summary`. |
| `version`, `category`, `tags` | No | Plugin defaults; profile may override display metadata | Catalog presentation and search. |

### Start with only what is necessary

The smallest useful workspace has three behavioral objects:

```python
Workspace(
    identifier="results",
    name="Results",
    description="Inspect complete result files.",
    source=ResultSource(),
    analysis=ResultAnalysis(),
    presentation=ResultPresentation(),
)
```

Add another object only when the workflow needs the behavior it owns:

| Requirement | Add or override | What becomes available |
| --- | --- | --- |
| Discover and open data | `Source` | Required catalog and item opening. `DirectorySource` handles common file trees. |
| Process the complete opened value | `Analysis.process()` | Required computation; receives `settings=None` when configuration is not overridden. |
| Display products | `Presentation.present()` | Required plots, tables, text, tabs, view switching, statistics, and display controls. |
| Add processing parameters | `Analysis.configure()` | Processing `number`, `select`, and `toggle` controls through `ParameterContext`. |
| Select buffers, windows, or events | `Delivery` | Static, seek, live, windowed, segmented, refresh, and overview behavior through `DeliveryContext`. |
| Read or write annotations | `Annotator` | Framework annotation menu, fields, timeline markers, and plot-bound selection values. |
| Download domain data | `Exporter` | Framework download menu with plugin-defined scopes and formats. |
| Add catalog columns | `DiscoveryColumn` values | Searchable and sortable per-item metadata. |

`process()` deliberately receives no UI object. It consumes delivered domain
data plus typed settings and returns domain products. This keeps numerical work
testable outside Sigvue. Timeline UI belongs to `Delivery`, processing controls
belong to `Analysis.configure()`, and display controls and layout belong to
`Presentation.present()`.

The factory does **not** construct `DeliveryContext`, `ParameterContext`,
`ViewContext`, `PageDefinition`, `PlaybackConfiguration`, or `OpenedItem`. The framework creates those objects
for each request. A source or its `DirectorySource.describe` callback creates
`DataResource` values during discovery, not normally in the factory itself.

Public object ownership is intentionally narrow:

| Public object | Who creates it? | Where it is used |
| --- | --- | --- |
| `Workspace` | Plugin factory | Returned from `create_workspace()`. |
| `Source` subclass or `DirectorySource` | Plugin factory | Passed as required `source=`. |
| `DirectorySource` | Plugin factory | Optional concrete replacement for writing a custom source. |
| `DataResource` | Source | Returned by `discover()`; later passed back to `open()`. |
| `Delivery` subclass | Plugin factory | Passed as optional `delivery=`. |
| `Analysis` subclass | Plugin | Passed as required `analysis=`; implements `process()` and optionally `configure()`. |
| `Presentation` subclass | Plugin | Passed as required `presentation=`; implements `present()`. |
| `DiscoveryColumn` | Plugin factory | Passed in optional `discovery_columns=`. |
| `Annotator` / `Exporter` | Plugin factory | Passed as optional capability objects. |
| `AnnotationField`, `CapabilityChoice` | Plugin capability | Advertise framework-rendered capability inputs. |
| `AnnotationRequest`, `ExportRequest` | Framework | Passed into plugin capability methods. |
| `DeliveryContext` | Framework | Passed into delivery for timeline and buffer selection. |
| `ParameterContext` | Framework | Passed into `configure`; exposes only typed parameter declarations. |
| `ViewContext` | Framework | Passed into `present`; exposes layout, display controls, views, and statistics. |
| `Segment` | Plugin delivery or analysis | Passed into `ui.segmented(...)`. |
| `TraceStyle` | Framework | Returned by `ui.trace_style(...)` for plotting code. |

A fully populated factory has this shape; every line marked optional may simply
be omitted:

```python
def create_workspace(config):
    return Workspace(
        identifier="my-analysis",                 # required fallback metadata
        name="My Analysis",                       # required fallback metadata
        description="Inspect domain recordings.", # required fallback metadata
        source=MySource(config["data_root"]),      # required Source
        delivery=MyDelivery(),                     # optional Delivery
        analysis=MyAnalysis(),                     # required Analysis object
        presentation=MyPresentation(),             # required Presentation object
        annotator=MyAnnotator(),                   # optional capability
        exporter=MyExporter(),                     # optional capability
        discovery_columns=MY_COLUMNS,              # optional catalog schema
        category="signal analysis",               # optional fallback metadata
        tags=("windowed", "domain-format"),        # optional fallback metadata
    )
```

### Contract relationships

![Contract relationships diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.24/docs/pypi-diagrams/02-contract-relationships.svg)

### Typed data path

`Source`, `Delivery`, `Analysis`, and `Presentation` are public generic base
objects. Pipeline-specific subclasses implement their named lifecycle methods.
Together their type parameters describe the complete data path:

![Typed data path diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.24/docs/pypi-diagrams/03-typed-data-path.svg)

The objects make every boundary explicit at construction time: the workspace
cannot accept a look-alike object that merely happens to have a method with the
right name. Type checkers can also verify individual `Source`, `Delivery`,
`Analysis`, and `Presentation` definitions.
The installed package includes a `py.typed` marker, so these checks also work
when `sigvue` is installed from a wheel.

Subclass the framework base objects when behavior needs state; this makes the
contract visible and lets type checkers verify each boundary:

```python
from collections.abc import Iterable

from sigvue.plugin import (
    Analysis,
    DataResource,
    Delivery,
    DeliveryContext,
    ParameterContext,
    Presentation,
    Source,
    ViewContext,
)


class MySource(Source[Recording]):
    def discover(self) -> Iterable[DataResource]:
        ...

    def open(self, resource: DataResource) -> Recording:
        ...


class WindowDelivery(Delivery[Recording, SampleWindow]):
    def prepare(
        self,
        recording: Recording,
        ui: DeliveryContext,
    ) -> SampleWindow:
        ...


class MyAnalysis(Analysis[SampleWindow, Settings, Products]):
    def configure(self, data: SampleWindow, ui: ParameterContext) -> Settings:
        ...

    def process(self, data: SampleWindow, settings: Settings | None) -> Products:
        ...


class MyPresentation(Presentation[Products]):
    def present(self, products: Products, ui: ViewContext) -> None:
        ...
```

`DirectorySource` handles the common filesystem case. All six behavioral base
classes are abstract, so incomplete pipeline objects fail immediately when
instantiated. At runtime, `Workspace` requires actual framework objects,
validates discovered `DataResource` values, and rejects duplicate resource
identifiers. There is one accepted
spelling for each boundary: `source=`, `delivery=`, `analysis=`,
`presentation=`, `annotator=`, and `exporter=`.

### Request lifecycle

The factory runs when the profile is loaded or reloaded. Source I/O, delivery,
configuration, processing, and presentation run later, when the browser opens
data or changes request state.

![Request lifecycle diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.24/docs/pypi-diagrams/04-request-lifecycle.svg)

`source.open()` is called for the selected item on each page request. A domain
reader may therefore be lightweight and read only the requested interval when
delivery calls it. Processing results are cached by item revision, timeline
state, and configuration values. Presentation-only controls and theme changes
reuse those products; changing processing parameters or the delivered interval
runs `process` again. Live and explicitly refreshing pages do not retain a
process result across requests.

### Minimal file-backed workspace

```python
# src/my_workspace/workspace.py
import json
from collections.abc import Mapping
from pathlib import Path
from dataclasses import dataclass
from typing import TypedDict

import plotly.graph_objects as go

from sigvue.plugin import Analysis, DirectorySource, ParameterContext, Presentation, ViewContext, Workspace


class ResultFile(TypedDict):
    values: list[float]


def load_result(path: Path) -> ResultFile:
    return json.loads(path.read_text())


@dataclass(frozen=True)
class Settings:
    scale: float


class ResultAnalysis(Analysis[ResultFile, Settings, list[float]]):
    def configure(self, result: ResultFile, ui: ParameterContext) -> Settings:
        return Settings(scale=float(ui.number("scale", label="Scale", default=1.0, step=0.1)))

    def process(self, result: ResultFile, settings: Settings | None) -> list[float]:
        if settings is None:
            raise RuntimeError("Result analysis requires configured settings")
        return [settings.scale * value for value in result["values"]]


class ResultPresentation(Presentation[list[float]]):
    def present(self, values: list[float], ui: ViewContext) -> None:
        figure = go.Figure(go.Scatter(y=values, name="Value"))
        with ui.tab("Values"):
            ui.place_parameters("scale", label="Processing")
            ui.plot(figure, key="values")


def create_workspace(config: Mapping[str, object]) -> Workspace:
    source = DirectorySource[ResultFile](
        Path(str(config["data_root"])),
        pattern="*.result.json",
        loader=load_result,
    )
    return Workspace(
        # Required fallback metadata; browser.toml may override it per instance.
        identifier="result-analysis",
        name="Result Analysis",
        description="Inspect result files.",
        # Required contracts.
        source=source,
        analysis=ResultAnalysis(),
        presentation=ResultPresentation(),
    )
```

This example uses all three lifecycle stages because it has a processing
parameter. The required contract is one source, one analysis, and one
presentation. When an `Analysis` subclass does not override `configure`, its
base implementation returns `None` to `process`. When overridden,
`configure` owns processing inputs, `process` remains domain code with no
presentation dependency, and `present` owns layout. `ui.place_parameters(...)` can put
a configured control inside a particular tab or switched view; otherwise it
remains in Details. Add delivery or capabilities only when the workflow needs
them.

Set `recursive=True` on `DirectorySource` to preserve nested directories in the
browser. The framework derives folder breadcrumbs from each file's path relative
to the source root; files are not flattened and directories are not presented as
fake analysis items. A custom source can provide the same behavior by setting
`DataResource(navigation_path=("campaign", "day-2"), ...)`.

### Discovery columns

Each workspace can declare the metadata columns shown beside discovered files.
The workspace supplies raw values in `DataResource.summary`; Sigvue owns table
rendering, null display, search, and sorting:

```python
from pathlib import Path

from sigvue.plugin import DataResource, DiscoveryColumn, Workspace

columns = (
    DiscoveryColumn("date", "Date", kind="datetime"),
    DiscoveryColumn("sample_rate", "Sampling rate", kind="si", unit="sample/s"),
    DiscoveryColumn("rf_frequency", "RF frequency", kind="si", unit="Hz"),
)

resource = DataResource(
    identifier="recording-1",
    title="Recording 1",
    source=Path("recording-1.sigmf-meta"),
    summary={
        "date": "2026-07-19T12:00:00Z",
        "sample_rate": 10_000_000,
        "rf_frequency": None,
    },
)

workspace = Workspace(
    # ...normal workspace arguments...
    discovery_columns=columns,
)
```

Column kinds are `text`, `number`, `datetime`, and `si`. Missing values remain
visible as unavailable values and sort after populated values in either sort
direction. Browser search includes titles, paths, tags, and every declared
summary value.

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
description = "Inspect the current campaign results"
category = "laboratory"
tags = ["campaign", "review"]

[workspaces.config]
data_root = "./data"
```

Top-level `id`, `name`, `description`, `category`, `tags`, and `icon` belong to
that displayed workspace instance and override the factory's default metadata.
This lets multiple entries use the same factory while appearing as distinct
workspaces. The factory receives `[workspaces.config]` for data and analysis
behavior. For compatibility, `id` and `name` are also present in `config`;
`profile_dir` is always supplied. Relative paths resolve from the directory
containing `browser.toml`.

```toml
[[workspaces]]
use = "my-analysis"
id = "campaign-a"
name = "Campaign A"
tags = ["field", "2026"]
[workspaces.config]
data_root = "./data/campaign-a"

[[workspaces]]
use = "my-analysis"
id = "campaign-b"
name = "Campaign B"
tags = ["laboratory", "reference"]
[workspaces.config]
data_root = "./data/campaign-b"
```

![browser.toml diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.24/docs/pypi-diagrams/05-browser-toml.svg)

These are two registered workspace instances, not two plugin implementations.
Their framework routes and catalog identities are isolated by their unique
top-level `id` values.

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

Without a delivery object, `process`—and `configure`, when supplied—receives
exactly what the source opened. A delivery object can prepare a different value
while leaving processing and presentation unchanged:

```python
from dataclasses import dataclass

from sigvue.plugin import Analysis, Delivery, DeliveryContext, ParameterContext, Presentation, ViewContext


@dataclass(frozen=True)
class SampleWindow:
    start_seconds: float
    samples: list[complex]


class FrameDelivery(Delivery[Recording, SampleWindow]):
    def prepare(
        self,
        recording: Recording,
        ui: DeliveryContext,
    ) -> SampleWindow:
        frame_seconds = ui.number("frame_seconds", default=0.1, minimum=0.001)
        position = ui.playback(
            mode="seek",
            duration=max(0.0, recording.duration - frame_seconds),
            step=0.01,
        )
        return SampleWindow(position, recording.read(position, frame_seconds))


class FrameAnalysis(Analysis[SampleWindow, Settings, Products]):
    def configure(self, window: SampleWindow, ui: ParameterContext) -> Settings:
        ...

    def process(self, window: SampleWindow, settings: Settings | None) -> Products:
        ...


class FramePresentation(Presentation[Products]):
    def present(self, products: Products, ui: ViewContext) -> None:
        ...
```

Pass `FrameDelivery()`, `FrameAnalysis()`, and `FramePresentation()` to the
corresponding `Workspace` fields. The framework
calls `source.open`, `delivery.prepare`, optional `configure`, `process`, and
`present` for the requested state, reusing cached products when only
presentation state changes.

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
physical-time values are `"ns"`, `"us"`, `"ms"`, `"s"`, `"min"`, `"h"`, and
`"d"`; `"auto"` chooses a sensible unit from the full duration. Editable boxes
display and accept that unit while delivery continues receiving canonical
seconds, so changing presentation units cannot change sample addressing or
persisted annotation times. `time_unit="samples"` is an explicit normalized
coordinate mode for data without a known sample rate; in that mode the pipeline
supplies and consumes sample coordinates instead of physical seconds.

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

When a view switcher selects among channels or collection members, delivery can
give the selector one overview per choice. The switcher key ties the two pieces
together; changing views redraws only the overview and does not move or
reprocess the selected window:

```python
start, end = ui.windowed(
    duration=recording.duration,
    default_window=0.1,
    overview_series=tuple(channel.power_summary() for channel in recording.channels),
    overview_durations=tuple(channel.duration for channel in recording.channels),
    overview_switcher="recording-channel",
    overview_label="Received power (dBFS)",
)

# Use the same key later in presentation.
ui.view_switcher("Channel", channel_figures, key="recording-channel", selector="dropdown")
```

`overview_durations` is optional. For collections whose members have different
lengths, it makes the framework display the selected member's actual start,
stop, width, and total duration. The requested interval remains expressed in
seconds; members shorter than that interval can clamp it to their available
range in their delivery implementation.

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

## Framework-created control objects

Plugins do not construct controls directly. The framework passes a restricted,
request-scoped API object into each behavioral method. Calling that object's
methods declares controls and returns the current typed value. An editor or
type checker therefore exposes only the operations valid at that lifecycle
stage.

| Receiving method | Framework API object | Available controls and helpers |
| --- | --- | --- |
| `Delivery.prepare(data, ui)` | `DeliveryContext` | `number`, `select`, `toggle`, `playback`, `windowed`, `segmented`, `refresh`, `once`, `time`, and `following_live`. |
| `Analysis.configure(data, ui)` | `ParameterContext` | Processing-only `number`, `select`, and `toggle`. Return an ordinary typed settings object. |
| `Analysis.process(data, settings)` | None | No browser API by design; perform deterministic domain processing. |
| `Presentation.present(products, ui)` | `ViewContext` | Display controls, layout, renderables, statistics, theme, and presentation caching. |
| `Annotator.fields` | `AnnotationField` values | Text, textarea, select, and number fields; optional Plotly-axis or box-selection bindings. |
| `Exporter.scopes` / `formats` | `CapabilityChoice` values | Plugin-defined download scope and format dropdown choices. |

`ViewContext` is the largest surface because it owns the displayed page:

| `ViewContext` method | Purpose |
| --- | --- |
| `number`, `select`, `toggle`, `color` | Declare display-only controls. |
| `colormap` | Add a compact colormap picker with low-to-high previews. |
| `limits` | Add validated paired numeric bounds. |
| `trace_style` | Add color, width, opacity, line-style, and marker controls; returns a Plotly-ready `TraceStyle`. |
| `tab` | Add a tab with a column layout and static or dynamic update policy. |
| `group` | Nest layout content in a row or column group. |
| `parameter_group` | Place controls declared in the current presentation region. |
| `place_parameters` | Place processing controls previously declared by `Analysis.configure()`. |
| `switcher` / `switcher_view` | Build arbitrary switched content incrementally. |
| `view_switcher` | Build button or dropdown switched views from a mapping. |
| `plot` | Display a native Plotly or Matplotlib figure. |
| `table`, `text`, `view` | Display mixed tabular, textual, or generic renderable content. |
| `stat` | Add workflow-specific details or diagnostics. |
| `once` | Cache presentation-only work by key and declared dependencies. |
| `theme` | Read the active `light` or `dark` theme for plugin-specific styling. |

Control ownership determines recomputation. Delivery and processing-control
changes can redeliver or reprocess data. Presentation-control changes rebuild
the view from cached products. Tabs and view switchers do not become processing
settings unless the plugin explicitly declares a processing control for that
purpose.

Plotly figures remain interactive and the framework does not resample or approximate
plugin data during transport. Matplotlib figures remain fully supported and are rendered server-side as responsive
PNG images. They provide a predictable CPU-rendered alternative when interactive
Plotly navigation is unnecessary. Tabs can mix Plotly, Matplotlib, tables, and text.
For plots whose data bounds are also their valid navigation bounds, set
`axis_navigation="bounded"` on `ui.plot` or `ui.view_switcher`. Sigvue derives
the limits from the explicit Plotly axis ranges, owns pan clamping and
double-click reset, and does not require framework-specific keys in the Plotly
figure metadata.

Use `update="static"` for item context that should be rendered once and
`update="dynamic"` for views that follow delivery. Expensive domain work
belongs in `process`, not a plot factory. A static plot factory can still name
presentation dependencies:

```python
with ui.tab("Reference", update="static"):
    ui.plot(
        lambda: make_reference_figure(data, threshold),
        key="reference",
        depends_on=("threshold",),
    )
```

## Optional annotation, export, and batch capabilities

Annotation and download are plugin-owned capabilities. If a workspace does not pass an
`annotator=` or `exporter=` to `Workspace`, the corresponding header menu is not
shown. The framework supplies typed field/choice helpers, renders the controls, and runs
exports on its background executor; the plugin decides how annotations are persisted and
how its domain data is serialized.

Batch is a separate workspace-level capability for work that should start from the
catalog rather than an open data view. A `Batch` advertises any combination of item
actions and workspace actions. Sigvue renders the action launcher at the left edge of
workspace cards and discovered-item rows, runs jobs on a dedicated background thread
pool, and retains pending, running, successful, or failed status while the application
is running. Successful jobs may expose one or more downloadable artifacts.

```python
from pathlib import Path
from sigvue.plugin import Batch, BatchDestination, BatchRequest, BatchResult, CapabilityChoice

class Reports(Batch[Recording]):
    item_actions = (CapabilityChoice("plot", "Build plot report"),)
    workspace_actions = (CapabilityChoice("all", "Compile workspace report"),)

    def item_destination(self, resource, request):
        name = f"{resource.identifier}.html"
        return BatchDestination(Path("reports/items"), (name,), "Report already generated")

    def workspace_destination(self, resources, request):
        return BatchDestination(Path("reports"), ("workspace.zip",), "Workspace report already generated")

    def run_item(self, resource, recording, request, directory):
        report = directory / f"{resource.identifier}.html"
        report.write_text(build_report(recording), encoding="utf-8")
        return BatchResult((report,), "Report generated")

    def run_workspace(self, resources, open_resource, request, directory):
        archive = compile_reports(resources, open_resource, directory)
        return BatchResult((archive,), "Workspace report generated")

workspace = Workspace(..., batch=Reports())
```

The plugin decides what “run” means, which actions exist at each scope, what data is
opened, where durable artifacts are stored, and which artifacts are produced. When a
destination declares expected filenames, Sigvue recognizes an already-completed action
after a server restart by checking those files. Omitting the destination hooks retains
the temporary-directory behavior; temporary results cannot be rediscovered after the
server exits. The framework owns scheduling, status, validation, polling, and downloads.

The same contract is available without starting the web server. First inspect the
actions and exact item identifiers exposed by a profile:

```bash
sigvue batch --config browser.toml --list
```

Then dispatch either a workspace action or an item action. The command prints pending,
running, and completed states, waits for the background job, and copies validated
artifacts into the requested directory:

```bash
sigvue batch --config browser.toml \
  --workspace lte-recordings --action report-all --output reports

sigvue batch --config browser.toml \
  --workspace lte-recordings \
  --item 'downlink::LTE_downlink_806MHz_2022-04-09_30720ksps' \
  --action report --output reports
```

Add `--json` for automation-friendly final status and artifact paths.

![Optional annotation, export, and batch capabilities diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.24/docs/pypi-diagrams/06-optional-annotation-export-and-batch-capabilities.svg)

Subclass `Annotator` to discover timeline annotations and add one from the current
delivered value. Subclass `Exporter` to advertise scope and format choices and write
one result file into the supplied directory. `CapabilityChoice`, `AnnotationField`,
`AnnotationPlotBinding`, `Annotation`, `AnnotationRequest`, and `ExportRequest` are
available from `sigvue.plugin`.
This keeps formats such as SigMF annotations, MAT, JSON, or a domain-specific archive out
of the framework.

Plot-oriented plugins can attach an `AnnotationPlotBinding` to a numeric
`AnnotationField`. When the annotation menu opens, Sigvue fills that input from the
currently visible lower or upper edge of the named axis. A pipeline can set
`selection_policy="box_preferred"` on the binding to prefer the latest compatible Plotly
box-selection bounds; deselecting or double-clicking clears the captured box. The plugin
declares the unit transform and may add the current playback position for buffer-relative
plot axes; the resulting editable value is still persisted entirely by the plugin.
When `view` names a view-switcher key instead of one concrete plot, Sigvue resolves the
binding against that switcher's active plot. The same selection is supplied to the
annotator as `AnnotationRequest.view_selections`, allowing a collection workspace to
persist into the selected member without turning that member choice into a processing
parameter. A discovered `Annotation` may carry the corresponding `view_selections`
mapping; Sigvue then shows its timeline marker only while those local view choices are
active.

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
| `POST /workspaces/{workspace_id}/batch` | Start a plugin-owned workspace batch action. |
| `POST /workspaces/{workspace_id}/items/{item_id}/batch` | Start a plugin-owned item batch action. |
| `GET /batches/{job_id}` | Poll batch status and discover result files. |
| `GET /batches/{job_id}/{filename}` | Download a completed batch artifact. |

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
python -m pip install -e ".[build,examples,test,release]"
python -m pytest -q tests
python -m pytest -q example_pipelines/tests
```

The publish workflow runs the framework and bundled-pipeline suites as separate
required steps before versioning or building a distribution.

Neutral, runnable workspace packages are maintained separately so the framework distribution stays format-independent: [Sigvue Examples](https://github.com/briday1/sigvue-examples).
