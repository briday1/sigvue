from __future__ import annotations

from datetime import datetime, timezone
from math import cos, log10, pi, sin

import plotly.graph_objects as go

from workspace_browser.core.models import ItemDescriptor, RefreshResult, WorkspaceMetadata
from workspace_browser.core.page import ControlSpec, OpenedItem, PageDefinition, PlaybackConfiguration, ViewSpec
from workspace_browser.core.status import ItemStatus
from workspace_browser.core.layout import container, view_slot


class GenericExampleWorkspace:
    @property
    def metadata(self) -> WorkspaceMetadata:
        return WorkspaceMetadata(
            identifier="generic-example",
            display_name="Generic Example Workspace",
            description="Example workspace that demonstrates discovery and viewing.",
            version="0.1.0",
            category="examples",
            tags=("example",),
        )

    def discover_items(self) -> list[ItemDescriptor]:
        now = datetime.now(tz=timezone.utc)
        return [
            ItemDescriptor(
                identifier="item-1",
                title="Example Item",
                subtitle="Demonstrates workspace item rendering",
                status=ItemStatus.READY,
                source_reference="memory://example/item-1",
                timestamp=now,
                tags=("example", "ready"),
                searchable_text="demo item",
            ),
            ItemDescriptor(
                identifier="sigmf-tone-demo",
                title="Four-Channel SigMF Collection Playback",
                subtitle="Recorded multi-channel capture played back in small analysis buffers",
                status=ItemStatus.READY,
                source_reference="sigmf://examples/two-tone.sigmf-meta",
                timestamp=now,
                tags=("sigmf", "iq", "timeseries", "spectrum"),
                summary_fields={"sample_rate": "1 MHz", "channels": "4", "samples": "2048/channel", "buffer": "128 samples"},
                searchable_text="sigmf collection playback four channel time frequency spectrum recording",
            ),
        ]

    def open_item(self, item_id: str) -> OpenedItem:
        items = {item.identifier: item for item in self.discover_items()}
        if item_id not in items:
            raise KeyError(item_id)
        item = items[item_id]
        if item_id == "sigmf-tone-demo":
            return self._open_sigmf_item(item)
        page = PageDefinition(
            title=item.title,
            subtitle=item.subtitle,
            status=item.status.value,
            views=(
                ViewSpec(name="summary", callback=lambda _: "# Example Item\nThis is a markdown view."),
            ),
            layout=container("tabs", (view_slot("summary"),)),
        )
        page.validate()
        return OpenedItem(item=item, page=page)

    def _open_sigmf_item(self, item: ItemDescriptor) -> OpenedItem:
        sample_rate = 1_000_000.0
        count = 2048
        channel_frequencies = ((55_000, 145_000), (82_000, 210_000), (115_000, 265_000), (165_000, 330_000))
        channels = []
        for channel_index, (primary, secondary) in enumerate(channel_frequencies):
            channels.append([
                0.72 * cos(2 * pi * primary * n / sample_rate + channel_index * 0.4)
                + (0.18 + 0.08 * sin(2 * pi * n / 700)) * cos(2 * pi * secondary * n / sample_rate)
                + 0.035 * sin(2 * pi * (n * 17 + channel_index * 31) / 97)
                for n in range(count)
            ])

        def playback_buffer(values: dict[str, object]) -> tuple[int, list[list[float]]]:
            window_size = int(values.get("buffer_size", 128))
            gain = float(values.get("amplitude_scale", 1.0))
            playback_time = float(values.get("__playback_time_seconds", 0.0))
            offset = (round(playback_time / 0.35) * window_size) % (count - window_size)
            return offset, [[gain * sample for sample in channel[offset : offset + window_size]] for channel in channels]

        def time_figures(values: dict[str, object]) -> list[go.Figure]:
            offset, buffers = playback_buffer(values)
            x = [(offset + index) / sample_rate for index in range(len(buffers[0]))]
            return [
                _plot(f"Channel {index + 1}", "Recording time (s)", "Amplitude", x, buffer)
                for index, buffer in enumerate(buffers)
            ]

        def frequency_figures(values: dict[str, object]) -> list[go.Figure]:
            _, buffers = playback_buffer(values)
            window_name = str(values.get("spectrum_window", "hann"))
            return [_spectrum_plot(f"Channel {index + 1}", buffer, sample_rate, window_name) for index, buffer in enumerate(buffers)]

        time_views = tuple(ViewSpec(name=f"time-channel-{index + 1}", callback=lambda values, i=index: time_figures(values)[i]) for index in range(4))
        frequency_views = tuple(ViewSpec(name=f"frequency-channel-{index + 1}", callback=lambda values, i=index: frequency_figures(values)[i]) for index in range(4))
        time_grid = container("grid", (view_slot(view.name) for view in time_views), label="Time Domain", columns=2)
        frequency_grid = container("grid", (view_slot(view.name) for view in frequency_views), label="Frequency Domain", columns=2)

        page = PageDefinition(
            title=item.title,
            subtitle=item.subtitle,
            status=item.status.value,
            controls=(
                ControlSpec(name="buffer_size", control_type="select", default=128, options=(64, 128, 256)),
                ControlSpec(name="amplitude_scale", control_type="select", default=1.0, options=(0.5, 1.0, 2.0, 4.0)),
                ControlSpec(name="spectrum_window", control_type="select", default="hann", options=("hann", "rectangular")),
            ),
            views=time_views + frequency_views,
            layout=container("tabs", (time_grid, frequency_grid)),
            playback=PlaybackConfiguration(enabled=True, duration_seconds=5.6, step_seconds=0.35, loop=True),
            metadata={
                "global": {"core:datatype": "rf32_le", "core:sample_rate": sample_rate, "core:num_channels": 4, "core:version": "1.2.5"},
                "captures": [{"core:sample_start": 0, "core:frequency": 915_000_000}],
                "collection": {"streams": [f"channel-{index + 1}.sigmf-meta" for index in range(4)]},
            },
        )
        page.validate()
        return OpenedItem(item=item, page=page)

    def refresh_item(self, item_id: str) -> RefreshResult:
        return RefreshResult(changed=False)


def _plot(title: str, x_label: str, y_label: str, x: list[float], y: list[float]) -> go.Figure:
    figure = go.Figure(go.Scatter(x=x, y=y, mode="lines", name=title))
    figure.update_layout(title=title, xaxis_title=x_label, yaxis_title=y_label, margin=dict(l=55, r=20, t=45, b=50))
    return figure


def _spectrum_plot(title: str, samples: list[float], sample_rate: float, window_name: str) -> go.Figure:
    size = min(128, len(samples))
    frequencies, magnitude = [], []
    for shifted in range(size):
        k = (shifted + size // 2) % size
        value = 0j
        for index, sample in enumerate(samples[:size]):
            weight = 0.5 - 0.5 * cos(2 * pi * index / (size - 1)) if window_name == "hann" else 1.0
            value += sample * weight * complex(cos(-2 * pi * k * index / size), sin(-2 * pi * k * index / size))
        frequencies.append((shifted - size // 2) * sample_rate / size / 1_000)
        magnitude.append(20 * log10(max(abs(value) / size, 1e-9)))
    return _plot(title, "Frequency (kHz)", "Magnitude (dBFS)", frequencies, magnitude)
