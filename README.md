# Scientific Workspace Browser

Scientific Workspace Browser turns file-backed analysis scripts into a local browser application. A workspace package decides:

1. Which items are available.
2. How an item is opened.
3. What data is delivered for one analysis run.
4. How that data is processed and displayed.

The framework supplies the catalog, page layout, parameters, themes, refresh and playback controls, plot updates, exports, and HTTP service.

## Install and run

```bash
python -m pip install workspace-browser
workspace-browser --config browser.toml
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
| Package entry point | Yes for profile loading | Give the factory a stable name for `browser.toml`. |

The data passed between these hooks is owned by the workspace package. The framework does not require a particular file format, array shape, reader, or analysis library.

### Minimal file-backed workspace

```python
# src/my_workspace/workspace.py
import json
from pathlib import Path

import plotly.graph_objects as go

from workspace_browser.plugin import AnalysisWorkspace, DirectorySource


def load_result(path: Path):
    return json.loads(path.read_text())


def analyze(result, ui):
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

Advertise the factory in the workspace package:

```toml
# pyproject.toml in the workspace package
[project.entry-points."workspace_browser.workspaces"]
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

The browser adds its `src` directory and reloads changed workspace modules when the page is refreshed. Use `--no-reload` to disable this behavior. A direct `module:factory` string is also accepted in `use`.

## Data delivery

Without a delivery object, `analyze` receives exactly what the source opened. A delivery object can prepare a different value while leaving analysis unchanged:

```python
class FrameDelivery:
    def prepare(self, recording, ui):
        frame_seconds = ui.number("frame_seconds", default=0.1, minimum=0.001)
        position = ui.playback(
            mode="seek",
            duration=max(0.0, recording.duration - frame_seconds),
            step=0.01,
        )
        return recording.read(position, frame_seconds)
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

For windowed selection, the workspace reads the returned interval and may provide a low-resolution overview statistic:

```python
start, end = ui.windowed(
    duration=recording.duration,
    default_window=0.1,
    minimum_window=0.001,
    step=0.001,
    overview=recording.summary_values(),
    overview_label="Activity",
)
return recording.read(start, end)
```

`overview` is optional. When supplied, it may be any finite 1D summary and does not need one value per sample. The framework distributes its values uniformly over the recording duration, so block statistics, sliding-window results, and decimated summaries all work. The framework draws and operates the range selector; tabs and exports receive only the value returned by the delivery policy.

For irregular stored results, provide explicit segment descriptors and use the returned descriptor to load the matching result:

```python
from workspace_browser.plugin import Segment

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

## Export and logging

Every opened item provides:

- **Download .mat**: the delivered data, controls, metadata, statistics, layout, and all registered views in one MATLAB structure.
- **Camera**: every Plotly and Matplotlib view, including hidden tabs and switched choices, as PNG files in one ZIP.

Exports run in a background executor. Static workspaces export the complete delivered value; seek and live workspaces export the current buffer; windowed workspaces export the selected interval; segmented workspaces export the selected result. Workspace packages do not implement export handlers when using `AnalysisWorkspace`.

Matplotlib PNG export requires no external browser. Plotly PNG export uses Kaleido, which is a required `workspace-browser` dependency. Kaleido 1.x uses Chrome or Chromium. If neither is installed, provision Plotly's compatible browser once:

```bash
plotly_get_chrome
```

Local file-backed seek and live items also provide **Log**. It stores timestamped review notes in a `logs/` directory beside the source and marks them on the timeline.

## HTTP API

The browser UI uses the same local JSON API available to integrations:

| Method and path | Result |
| --- | --- |
| `GET /health` | Service health. |
| `GET /workspaces` | Registered workspaces. |
| `GET /workspaces/{workspace_id}/items` | Discovered items. |
| `GET /workspaces/{workspace_id}/items/{item_id}` | Page definition and rendered views. Query parameters carry controls and timeline state. |
| `GET /workspaces/{workspace_id}/items/{item_id}/exports?format=mat|png` | Start a background export. |
| `GET /exports/{job_id}` | Poll export status. |
| `GET /exports/{job_id}/{filename}` | Download a completed export. |
| `POST /workspaces/{workspace_id}/items/{item_id}/logs` | Write a progress note for a seek/live item. |

## PyPI and standalone distribution

The PyPI wheel contains:

- The browser server and both export implementations.
- Dependency metadata that installs Plotly, Matplotlib, and Kaleido.
- The PyInstaller spec and Kaleido runtime hook under `workspace_browser._packaging`.
- The `workspace-browser-build` command.

Chrome is intentionally not placed in the platform-independent Python wheel. A normal installation uses an existing Chrome/Chromium or the copy installed by `plotly_get_chrome`.

To build a platform-specific, one-file executable with Chrome included:

```bash
python -m pip install "workspace-browser[build]"
workspace-browser-build
```

The result is `dist/workspace-browser` or `dist/workspace-browser.exe`. Build separately on Windows, Linux, and macOS. To create a smaller executable that relies on a browser installed on the target machine:

```bash
SWB_BUNDLE_CHROME=0 workspace-browser-build
```

In PowerShell, set `$env:SWB_BUNDLE_CHROME = "0"` before running the command.

Workspace packages, `browser.toml`, and data remain external to the executable.

## Development

```bash
python -m pip install -e ".[build]"
PYTHONPATH=src python -m unittest discover -s tests -q
```

Neutral, runnable workspace packages are maintained separately so the framework distribution stays format-independent: [Scientific Workspace Browser Examples](https://github.com/briday1/Scientific-Workspace-Browser-Examples).
