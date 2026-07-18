import unittest
from datetime import datetime, timezone

from sigvue.catalog.browser import sort_items
from sigvue.core.models import ItemDescriptor


class CatalogTests(unittest.TestCase):
    def test_sort_by_timestamp_handles_missing_values(self):
        older = datetime(2025, 1, 1, tzinfo=timezone.utc)
        newer = datetime(2025, 2, 1, tzinfo=timezone.utc)
        items = [
            ItemDescriptor(identifier="none", title="none", timestamp=None),
            ItemDescriptor(identifier="new", title="new", timestamp=newer),
            ItemDescriptor(identifier="old", title="old", timestamp=older),
        ]
        sorted_items = sort_items(items, by="timestamp")
        self.assertEqual(["old", "new", "none"], [item.identifier for item in sorted_items])


if __name__ == "__main__":
    unittest.main()
