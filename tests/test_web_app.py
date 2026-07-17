import unittest
from unittest.mock import Mock

from plotly.graph_objects import Figure

from workspace_browser.examples.generic import GenericExampleWorkspace
from workspace_browser.web.application import _make_handler, create_app


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
        self.assertEqual("markdown", payload["page"]["rendered_views"][0]["kind"])

    def test_sigmf_example_has_time_and_frequency_tabs(self):
        app = create_app()
        items = app.list_items("generic-example", {})
        self.assertIn("sigmf-tone-demo", {item["id"] for item in items})
        payload = app.open_item("generic-example", "sigmf-tone-demo")
        self.assertEqual("tabs", payload["page"]["layout"]["kind"])
        self.assertEqual(8, len(payload["page"]["views"]))
        self.assertEqual(["Time Domain", "Frequency Domain"], [child["props"]["label"] for child in payload["page"]["layout"]["children"]])
        self.assertTrue(all(view["kind"] == "plotly" for view in payload["page"]["rendered_views"]))
        time_playback = payload["page"]["rendered_views"][0]["value"]
        self.assertIn("data", time_playback)
        self.assertIn("layout", time_playback)
        self.assertTrue(payload["page"]["playback"]["enabled"])

        opened = GenericExampleWorkspace().open_item("sigmf-tone-demo")
        self.assertIsInstance(opened.page.views[0].callback({}), Figure)

    def test_workspace_controls_change_rendered_playback(self):
        app = create_app()
        payload = app.open_item(
            "generic-example",
            "sigmf-tone-demo",
            {"buffer_size": "256", "amplitude_scale": "0.5", "spectrum_window": "rectangular", "__playback_time_seconds": "0.7"},
        )
        page = payload["page"]
        self.assertEqual(3, len(page["controls"]))
        playback = page["rendered_views"][0]["value"]
        self.assertEqual(256, len(playback["data"][0]["x"]))

    def test_launch_url_serves_browser_interface(self):
        app = create_app()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/"
        handler._write_html = Mock()
        handler.do_GET()
        body = handler._write_html.call_args.args[0]
        self.assertIn("Scientific Workspace Browser", body)
        self.assertIn("catalog()", body)
        self.assertIn('/assets/plotly.min.js', body)
        self.assertIn("updatePlotlyViews(p.rendered_views)", body)

    def test_plotly_javascript_is_served(self):
        app = create_app()
        handler_type = _make_handler(app)
        handler = handler_type.__new__(handler_type)
        handler.path = "/assets/plotly.min.js"
        handler._write_javascript = Mock()
        handler.do_GET()
        javascript = handler._write_javascript.call_args.args[0]
        self.assertIn("plotly.js", javascript)


if __name__ == "__main__":
    unittest.main()
