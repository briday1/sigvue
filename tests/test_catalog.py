import unittest
from datetime import datetime, timezone

from sigvue.catalog.browser import search_items, sort_items
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

    def test_summary_fields_are_searchable_and_sortable_with_nulls_last(self):
        items = [
            ItemDescriptor("none", "Unknown", tags=("quiet",), summary_fields={"sample_rate": None}),
            ItemDescriptor("fast", "Fast", summary_fields={"sample_rate": 10_000_000.0}),
            ItemDescriptor("slow", "Slow", summary_fields={"sample_rate": 2_000_000.0}),
        ]
        self.assertEqual(["fast"], [item.identifier for item in search_items(items, "10000000")])
        self.assertEqual(["none"], [item.identifier for item in search_items(items, "quiet")])
        self.assertEqual(
            ["slow", "fast", "none"],
            [item.identifier for item in sort_items(items, by="sample_rate")],
        )


if __name__ == "__main__":
    unittest.main()
