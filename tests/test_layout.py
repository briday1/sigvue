import unittest

from sigvue.core.errors import InvalidLayoutError
from sigvue.core.layout import (
    container,
    control_slot,
    selected_view_names,
    validate_layout,
    view_slot,
)
from sigvue.core.page import PageDefinition, PlaybackConfiguration, ViewSpec


class LayoutTests(unittest.TestCase):
    def test_valid_layout(self):
        page = PageDefinition(
            title="Title",
            views=(ViewSpec(name="a", callback=lambda _: "text"),),
            layout=container("tabs", (view_slot("a"),)),
        )
        page.validate()

    def test_unknown_view_fails_validation(self):
        with self.assertRaises(InvalidLayoutError):
            validate_layout(view_slot("missing"), {"a"})

    def test_control_slots_must_reference_declared_controls(self):
        validate_layout(container("control_group", (control_slot("threshold"),)), set(), {"threshold"})
        with self.assertRaises(InvalidLayoutError):
            validate_layout(control_slot("missing"), set(), {"threshold"})

    def test_window_overview_switcher_must_exist_in_the_layout(self):
        page = PageDefinition(
            title="Title",
            views=(ViewSpec(name="a", callback=lambda _: "text"),),
            layout=container("tabs", (view_slot("a"),)),
            playback=PlaybackConfiguration(
                mode="windowed",
                duration_seconds=10.0,
                window_end_seconds=1.0,
                minimum_window_seconds=0.1,
                overview_values=(1.0,),
                overview_series=((1.0,),),
                overview_switcher_key="channel",
            ),
        )
        with self.assertRaisesRegex(ValueError, "view switcher"):
            page.validate()

    def test_selected_views_follow_tabs_and_multidimensional_switchers(self):
        switcher = container(
            "view_switcher",
            (view_slot("time-all"), view_slot("time-ch1"), view_slot("frequency-all")),
            key="waterfall",
            selection_keys=("waterfall:0", "waterfall:1"),
            coordinates=((0, 0), (0, 1), (1, 0)),
        )
        layout = container(
            "tabs",
            (container("grid", (view_slot("summary"),)), container("grid", (switcher,))),
            selection_key="__tabs",
        )

        self.assertEqual(("summary",), selected_view_names(layout))
        self.assertEqual(
            ("time-ch1",),
            selected_view_names(layout, {
                "__view_selection___tabs": "1",
                "__view_selection_waterfall:0": "0",
                "__view_selection_waterfall:1": "1",
            }),
        )

if __name__ == "__main__":
    unittest.main()
