# Bundled examples

These are the small, copyable examples shipped with the Sigvue repository.
They deliberately live outside `src/sigvue`: SigMF is an application format,
not framework core.

```text
examples/
├── formats/           framework-independent SigMF I/O and writing
├── readers/           SigMF discovery and buffering adapters for Sigvue
├── capabilities/      optional SigMF annotation and export behavior
├── style/             shared Plotly styling and annotation overlays
├── comms/
│   ├── analysis.py    ordinary numerical processing
│   ├── plots.py       ordinary Plotly functions
│   └── sigvue.py      Reader + one view callback + Workspace
├── waterfall/
│   ├── analysis.py
│   ├── models.py
│   ├── plots.py
│   └── sigvue.py
├── scripts/
│   ├── generate_all.py
│   ├── generate_comms.py
│   └── generate_lte.py
└── browser.toml
```

Generate the QPSK/16-QAM/64-QAM and LTE SigMF recordings, then launch:

```bash
python -m pip install -e ".[examples]"
python -m examples.scripts.generate_all
sigvue --config examples/browser.toml
```

Open <http://127.0.0.1:8000>. Generated data is untracked. Use the individual
generator modules when only one data group is needed.

## The interface

Each example returns one `Workspace`:

```python
from sigvue import Workspace
from sigvue.helpers import WorkspaceConfig
from examples.capabilities.sigmf import (
    SigMFExporter,
    WaterfallSigMFAnnotator,
)
from examples.formats.sigmf import power_overview
from examples.readers import SIGMF_DISCOVERY_COLUMNS, sigmf_reader


def create_reader(config):
    root = WorkspaceConfig(config).path("data_root")
    return sigmf_reader(
        root,
        pattern="*.sigmf-meta",
        describe=describe_sigmf_recording,
        recursive=True,
    ).windowed(
        read_interval,
        duration=lambda recording: recording.duration_seconds,
        default=0.012,
        minimum=0.004,
        step=0.002,
        overview=power_overview,
        overview_label="Mean received power (dBFS)",
        time_unit="ms",
    )


def view(data, ui):
    defaults = WaterfallSettings()
    settings = WaterfallSettings(
        fft_size=int(ui.select(
            "fft_size",
            label="FFT size (samples)",
            default=defaults.fft_size,
            options=(256, 512, 1024, 2048, 4096),
            group="Spectrogram processing",
        )),
        overlap_percent=int(ui.select(
            "overlap_percent",
            label="Overlap (%)",
            default=defaults.overlap_percent,
            options=(0, 25, 50, 75),
            group="Spectrogram processing",
        )),
    )
    products = ui.compute(
        "waterfall-analysis",
        lambda: analyze(data, settings),
    )
    colormap = ui.colormap(
        "colormap",
        label="Waterfall colormap",
        default="Plasma",
        options=COLORMAPS,
        group="Display",
    )
    with ui.tab("Spectrum + waterfall"):
        ui.plot(
            lambda: waterfall_figure(products, colormap=colormap),
            key="lte-waterfall",
            axis_navigation="bounded",
        )


def create_workspace(config):
    return Workspace(
        identifier="synthetic-lte-waterfall",
        name="Synthetic LTE Waterfall",
        description="Inspect exact LTE recording windows.",
        reader=create_reader(config),
        view=view,
        annotator=WaterfallSigMFAnnotator(
            "lte-waterfall",
            "annotation_region_color",
        ),
        exporter=SigMFExporter(),
        discovery_columns=SIGMF_DISCOVERY_COLUMNS,
    )
```

The example factories require `data_root` from their `browser.toml` entry.
They do not contain a second fallback path.

There is no framework-defined processing/presentation boundary. `view(data,
ui)` is the workspace's unrestricted domain callback. It owns the controls,
optional cached computation, statistics, tabs, and plots directly. Ordinary
`analyze` and plot-builder functions stay reusable without becoming framework
stages.

In this repository, **workspace** and **pipeline** are not synonyms. A
`Workspace` is the single Sigvue object that binds a reader, a view callback,
browser identity, and optional capabilities. A pipeline is ordinary,
user-owned analysis and plotting code such as `analyze()` and
`plot_waterfall()`; Sigvue neither defines nor requires a `Pipeline` type.

`ui.compute(...)` is optional. Here it avoids repeating shared analysis when a
display-only control or selected tab changes. Work needed by only one view may
instead stay inside that view's lazy lambda.

## Headless reuse

The same pieces work without the browser:

```python
reader = create_reader(config)
reference = reader.discover()[0]
window = reader.load(reference, start=0.0, stop=0.012)
settings = WaterfallSettings()
products = analyze(window, settings)
figure = plot_waterfall(products)
```

Reader windows are exact. Browser rendering limits may rasterize a heatmap for
the current viewport, but they do not modify the loaded or analyzed scientific
data.

## Layout preservation

The nested `ui` calls in each workspace's `view` function remain the source of
truth for the page:

- tabs and weighted grids;
- multiple simultaneous view-switcher dimensions;
- inline and details-sidebar controls;
- tables, text, Plotly, and Matplotlib views;
- annotation, export, timeline, and viewport behavior.

The interface change does not flatten or replace any of those layouts.

## Reusable helpers

| Helper | Responsibility |
| --- | --- |
| `Files` / `Reader` | Preserve native references and provide exact loading or buffering. |
| `formats.sigmf` | Read and write exact SigMF recordings without importing Sigvue. |
| `readers.sigmf_reader()` | Connect SigMF discovery and exact reads to a workspace. |
| `formats.sigmf.power_overview()` | Build a bounded navigation overview. |
| `capabilities.sigmf.SigMFAnnotator` | Read and persist standard SigMF annotation regions. |
| `style.add_time_frequency_annotation_regions()` | Draw hoverable vector annotation regions independently of heatmap rasterization. |
| `capabilities.sigmf.SigMFExporter` | Export current buffers or complete recordings. |

These modules are named for their responsibilities. A project imports only the
format, reader, capability, or style helper it needs.

## Test

```bash
python -m pytest -q examples/tests
```
