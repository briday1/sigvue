import unittest

from sigvue.core.errors import InvalidLayoutError
from sigvue.core.layout import container, control_slot, validate_layout, view_slot
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

if __name__ == "__main__":
    unittest.main()
