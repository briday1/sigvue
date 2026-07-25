<!-- Generated from README.md by scripts/build_pypi_readme.py. Do not edit directly. -->

# Sigvue

Sigvue turns a file-backed scientific script into a local browser application.
It does not impose separate processing and presentation stages.

If you can read and display your data, you already have the application:

```python
def view(data, ui):
    products = analyze(data)
    with ui.tab("Results"):
        ui.plot(lambda: plot(products), key="results")
```

The author returns one `Workspace`. A reusable `Reader` handles discovery and
exact buffering; one unrestricted `view(data, ui)` callback does everything
after that.

## Install and run

```bash
python -m pip install sigvue
sigvue --config browser.toml
```

Open <http://127.0.0.1:8000>.

## The API

![The API diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.44/docs/pypi-diagrams/01-the-api.svg)

There is one application object:

```python
Workspace(
    identifier="my-data",
    name="My Data",
    description="Inspect recordings.",
    reader=reader,
    view=view,
)
```

Inside `view`, the author decides what is shared, what is deferred, and whether
processing and plotting are separate at all.

## Minimal file-backed workspace

```python
from pathlib import Path

import plotly.graph_objects as go

from sigvue import Files, Workspace


def open_samples(path: Path) -> tuple[float, ...]:
    return tuple(float(value) for value in path.read_text().split(","))


def view(samples, ui):
    gain = float(ui.number("gain", default=2.0, minimum=0.0))
    values = tuple(value * gain for value in samples)

    ui.stat("Samples", len(samples))
    with ui.tab("Values"):
        ui.plot(
            lambda: go.Figure(go.Scatter(y=values)),
            key="values",
        )


def create_workspace(config=None):
    return Workspace(
        identifier="values",
        name="Values",
        description="Inspect sample files.",
        reader=Files("data", "*.samples", open_samples),
        view=view,
    )
```

There is no framework boundary between multiplication and plotting. The
callback can call one combined function just as easily:

```python
def view(samples, ui):
    with ui.tab("Values"):
        ui.plot(lambda: process_and_plot(samples), key="values")
```

That lambda is lazy. Hidden tabs and switcher choices are not executed.

## Headless data access

`Reader.discover()` returns native author-owned references. Paths stay `Path`
objects; grouped collections and database keys stay domain objects.

```python
reader = Files("data", "*.samples", open_samples)
path = reader.discover()[0]
samples = reader.load(path)
figure = process_and_plot(samples)
```

The same reader used by the browser is useful in scripts, notebooks, tests, and
batch jobs.

### Windowed data

```python
reader = Files(root, "*.bin", open_recording).windowed(
    read_window,  # read_window(recording, start, stop)
    duration=lambda recording: recording.duration_seconds,
    default=0.100,
    minimum=0.010,
    step=0.010,
    overview=power_overview,
    overview_label="Median power",
)
```

Headlessly:

```python
window = reader.load(path, start=2.0, stop=2.1)
```

Opened recordings, recent exact buffers, and overviews are revision-aware
cached for repeated browser requests. Reader buffering never approximates
scientific data.

### Segmented data

```python
from sigvue import Segment

reader = Files(root, "*.json", open_collection).segmented(
    read_segment,  # read_segment(collection, selected_segment)
    duration=lambda collection: collection.duration,
    segments=lambda collection: tuple(
        Segment(event.id, event.start, event.duration, event.label)
        for event in collection.events
    ),
)
```

The callback receives the selected `Segment`, so irregular events and scans
retain their identity.

### Seek and live playback

```python
reader = Files(root, "*.bin", open_recording).playback(
    read_window,
    duration=lambda recording: recording.duration_seconds,
    default=0.020,
    minimum=0.001,
    maximum=0.100,
    buffer_step=0.001,
    mode="live",
    seek_step=0.010,
    refresh_interval=1.0,
)
```

This supplies an exact moving buffer. When `maximum` is given, the browser also
exposes its width.

### Application-specific buffering

If a domain needs coupled controls that do not fit the regular helpers, the
reader boundary stays open:

```python
reader = Files(root, "*.collection", open_collection).buffered(
    read_buffer,       # ordinary headless read(opened, ...)
    select_buffer,     # select_buffer(opened, ui)
)
```

`reader.load(reference, ...)` calls `read_buffer` directly. The browser calls
`select_buffer`, which may declare buffer controls and then use that same exact
read. This does not introduce another processing or presentation stage.

## Processing, caching, and lazy work

Sigvue does not decide how the callback is divided.

Direct processing is ordinary Python:

```python
def view(data, ui):
    threshold = float(ui.number("threshold", default=3.0))
    events = detect_events(data, threshold)
    with ui.tab("Events"):
        ui.plot(lambda: plot_events(data, events), key="events")
```

For shared expensive work, `ui.compute` is an optional generic cache:

```python
def view(data, ui):
    threshold = float(ui.number("threshold", default=3.0))
    events = ui.compute(
        "event-detection",
        lambda: detect_events(data, threshold),
    )
    with ui.tab("Events"):
        ui.plot(lambda: plot_events(data, events), key="events")
    with ui.tab("Table"):
        ui.table(lambda: event_rows(events), key="event-table")
```

By default, the cache key includes:

- the reader/source revision;
- the selected window, segment, or playback position;
- every control declared before `ui.compute`.

The author may specify `depends_on=(...)` to narrow control dependencies, or
ignore `ui.compute` entirely.

Slow work needed by only one view belongs inside that view:

```python
def view(data, ui):
    with ui.tab("Overview"):
        ui.plot(lambda: plot_overview(data), key="overview")

    with ui.tab("Slow diagnostics"):
        ui.plot(
            lambda: analyze_and_plot_diagnostics(data),
            key="diagnostics",
        )
```

Opening Overview does not run the diagnostics.

## Exact complex layouts

The same callback supports all current layouts without another abstraction:

```python
def view(data, ui):
    reference = str(ui.select(
        "phase_reference",
        default="Channel 1",
        options=channel_names(data),
    ))
    calibrated = ui.compute(
        "calibrated-radar",
        lambda: calibrate(data, phase_reference=reference),
    )

    ui.stat("Channels", calibrated.channel_count)

    with ui.tab("Waterfall"):
        ui.view_switcher(
            ("Domain", "Channel"),
            waterfall_views(calibrated, ui),
            key="waterfall-domain",
            selector=("buttons", "dropdown"),
            axis_navigation="bounded",
        )

    with ui.tab("Calibration", columns=(1, 1)):
        ui.plot(lambda: phase_plot(calibrated), key="phase")
        ui.table(calibrated.calibration_rows, key="calibration")
```

Tabs, weighted grids, nested groups, multidimensional switchers, display
controls, inline processing controls, tables, text, and deferred plots all stay
in the one nested `ui` API.

![Exact complex layouts diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.44/docs/pypi-diagrams/02-exact-complex-layouts.svg)

## Custom discovery metadata

Paths receive useful defaults. Custom references may provide `describe`:

```python
from sigvue import DataResource, Reader

reader = Reader(
    discover=discover_sequences,
    open=open_sequence,
    describe=lambda sequence: DataResource(
        identifier=sequence.id,
        title=sequence.title,
        source=sequence,
        timestamp=sequence.timestamp,
        tags=("radar", sequence.station),
        summary={"scan_count": sequence.scan_count},
    ),
)
```

Optional `DiscoveryColumn` values on `Workspace` define typed catalog columns.
Recursive readers preserve their relative directories as browser folders by
default. Set `flatten_discovery=True` on `Workspace` to show every discovered
item at the workspace root without changing its identifier, source, or reader.

## Optional capabilities

Annotation, export, and batch support remain independent:

```python
Workspace(
    ...,
    reader=reader,
    view=view,
    annotator=MyAnnotator(),
    exporter=MyExporter(),
    batch=MyBatchActions(),
    discovery_columns=MY_COLUMNS,
)
```

These contracts are imported directly from `sigvue`. Format-specific readers
and capabilities belong with the application or examples, not in Sigvue core.
Batch actions run independently of the current browser route. The notification
bell shows queued and running actions, follows them while the user browses other
workspaces or views, and retains their completed outputs for opening or path
copying. Reloading the page reconnects to jobs still owned by the server.

![Optional capabilities diagram](https://raw.githubusercontent.com/briday1/sigvue/v2026.44/docs/pypi-diagrams/03-optional-capabilities.svg)

## Configuration

A package exposes `create_workspace(config)`:

```toml
[project.entry-points."sigvue.workspaces"]
my-analysis = "my_package.sigvue:create_workspace"
```

`browser.toml` chooses instances:

```toml
[browser]
title = "My data browser"

[[workspaces]]
use = "my-analysis"
id = "recordings"
name = "Recordings"

[workspaces.config]
data_root = "./data"
```

The factory reads that location with
`WorkspaceConfig(config).path("data_root")`. The bundled examples deliberately
provide no code-level fallback, so `browser.toml` is their single source of
truth for data locations. The same factory may still be reused in several TOML
entries with different roots and metadata.

When a profile contains exactly one enabled workspace, the browser opens that
workspace's item discovery directly at `/`. Profiles with multiple workspaces
retain the searchable workspace catalog. Workspace code and configuration are
identical in both cases.

## Runnable examples

Small examples live under [`examples/`](examples/README.md). The standalone examples
distribution covers communications, LTE waterfalls, calibrated multi-channel
radar, annotated ECG, weather radar, passive acoustics, seismology, stored
events, and native planetary data.

```bash
python -m pip install -e ".[examples]"
python -m examples.scripts.generate_all
sigvue --config examples/browser.toml
```

## PyPI documentation

PyPI does not render Mermaid. The release workflow renders the diagrams:

```bash
python scripts/build_pypi_readme.py --ref "v$VERSION"
```

`scripts/puppeteer-ci.json` supplies Chromium's CI sandbox arguments.

## Development

```bash
python -m pip install -e ".[test,release]"
python -m pytest -q
python -m build
python -m twine check dist/*
```
