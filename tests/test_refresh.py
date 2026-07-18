import unittest

from sigvue.core.models import RefreshResult
from sigvue.refresh.manager import RefreshManager


class RefreshManagerTests(unittest.TestCase):
    def test_overlap_prevention(self):
        manager = RefreshManager()
        first = manager.begin_refresh("item")
        second = manager.begin_refresh("item")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_stale_result_rejection(self):
        manager = RefreshManager()
        first = manager.begin_refresh("item")
        manager.complete_refresh("item", generation=first, result=RefreshResult(changed=False))
        second = manager.begin_refresh("item")
        applied = manager.complete_refresh("item", generation=first, result=RefreshResult(changed=True))
        self.assertFalse(applied)
        manager.complete_refresh("item", generation=second, result=RefreshResult(changed=True))


if __name__ == "__main__":
    unittest.main()
