import unittest

from workspace_browser.core.errors import DuplicateWorkspaceError
from workspace_browser.core.models import WorkspaceMetadata
from examples.generic import GenericExampleWorkspace
from workspace_browser.registry.registry import WorkspaceRegistry


class DuplicateWorkspace(GenericExampleWorkspace):
    @property
    def metadata(self):
        return WorkspaceMetadata(
            identifier="generic-example",
            display_name="Duplicate",
            description="duplicate",
            version="0.1.0",
        )


class RegistryTests(unittest.TestCase):
    def test_register_and_list(self):
        registry = WorkspaceRegistry()
        registry.register(GenericExampleWorkspace())
        self.assertEqual(1, len(registry.list()))

    def test_duplicate_workspace_ids_are_rejected(self):
        registry = WorkspaceRegistry()
        registry.register(GenericExampleWorkspace())
        with self.assertRaises(DuplicateWorkspaceError):
            registry.register(DuplicateWorkspace())


if __name__ == "__main__":
    unittest.main()
