import unittest
from types import SimpleNamespace

from workspace_browser.registry.discovery import _load_entrypoint


class DiscoveryTests(unittest.TestCase):
    def test_discovery_failure_isolated(self):
        loaded = []
        failures = []

        class FailingEntryPoint:
            name = "broken"
            dist = SimpleNamespace(name="pkg")

            def load(self):
                raise RuntimeError("boom")

        _load_entrypoint(FailingEntryPoint(), loaded, failures)
        self.assertEqual([], loaded)
        self.assertEqual(1, len(failures))
        self.assertEqual("broken", failures[0].entry_point)


if __name__ == "__main__":
    unittest.main()
