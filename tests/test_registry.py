import unittest

from sigvue.core.errors import DuplicateWorkspaceError
from sigvue.core.models import WorkspaceMetadata
from sigvue.registry.registry import WorkspaceRegistry


class StubWorkspace:
    def __init__(self, name="Primary"):
        self.metadata = WorkspaceMetadata("stub", name, "test", "0.1.0")


class RegistryTests(unittest.TestCase):
    def test_register_and_list(self):
        registry = WorkspaceRegistry()
        registry.register(StubWorkspace())
        self.assertEqual(1, len(registry.list()))

    def test_duplicate_workspace_ids_are_rejected(self):
        registry = WorkspaceRegistry()
        registry.register(StubWorkspace())
        with self.assertRaises(DuplicateWorkspaceError):
            registry.register(StubWorkspace("Duplicate"))


if __name__ == "__main__":
    unittest.main()
