class WorkspaceBrowserError(Exception):
    """Base error for the framework."""


class DuplicateWorkspaceError(WorkspaceBrowserError):
    """Raised when two workspaces share the same identifier."""


class WorkspaceLoadError(WorkspaceBrowserError):
    """Raised when a plugin workspace fails to load."""


class InvalidLayoutError(WorkspaceBrowserError):
    """Raised when a page layout is invalid."""
