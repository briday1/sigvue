from __future__ import annotations

from dataclasses import dataclass, field

from sigvue.core.contracts import Workspace
from sigvue.core.errors import DuplicateWorkspaceError


@dataclass
class WorkspaceRegistry:
    _workspaces: dict[str, Workspace] = field(default_factory=dict)

    def register(self, workspace: Workspace) -> None:
        identifier = workspace.metadata.identifier
        if identifier in self._workspaces:
            raise DuplicateWorkspaceError(f"Workspace '{identifier}' is already registered")
        self._workspaces[identifier] = workspace

    def get(self, workspace_id: str) -> Workspace:
        return self._workspaces[workspace_id]

    def list(self) -> list[Workspace]:
        return list(self._workspaces.values())
