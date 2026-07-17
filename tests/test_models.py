import unittest

from workspace_browser.core.models import ItemDescriptor
from workspace_browser.core.status import ItemStatus


class ModelTests(unittest.TestCase):
    def test_item_descriptor_normalizes_status(self):
        item = ItemDescriptor(identifier="1", title="Title", status="READY")
        self.assertEqual(ItemStatus.READY, item.status)

    def test_item_descriptor_rejects_empty_id(self):
        with self.assertRaises(ValueError):
            ItemDescriptor(identifier="", title="Title")


if __name__ == "__main__":
    unittest.main()
