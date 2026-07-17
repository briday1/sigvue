import unittest

from workspace_browser.core.errors import InvalidLayoutError
from workspace_browser.core.layout import container, validate_layout, view_slot
from workspace_browser.core.page import PageDefinition, ViewSpec


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


if __name__ == "__main__":
    unittest.main()
