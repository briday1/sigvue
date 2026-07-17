import unittest

from workspace_browser.web.application import create_app


class WebAppTests(unittest.TestCase):
    def test_create_app_has_example_workspace(self):
        app = create_app()
        workspaces = app.list_workspaces()
        self.assertEqual(1, len(workspaces))

    def test_open_item_returns_layout_and_views(self):
        app = create_app()
        payload = app.open_item("generic-example", "item-1")
        self.assertEqual("item-1", payload["item"]["id"])
        self.assertIn("summary", payload["page"]["views"])


if __name__ == "__main__":
    unittest.main()
