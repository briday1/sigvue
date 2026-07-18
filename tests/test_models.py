import unittest

from sigvue.core.models import ItemDescriptor


class ModelTests(unittest.TestCase):
    def test_item_descriptor_keeps_discovery_metadata(self):
        item = ItemDescriptor(identifier="1", title="Title", tags=("sigmf",))
        self.assertEqual(("sigmf",), item.tags)

    def test_item_descriptor_rejects_empty_id(self):
        with self.assertRaises(ValueError):
            ItemDescriptor(identifier="", title="Title")


if __name__ == "__main__":
    unittest.main()
