from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ItemDescriptor, RefreshResult, WorkspaceMetadata
from .page import OpenedItem


@runtime_checkable
class Workspace(Protocol):
    @property
    def metadata(self) -> WorkspaceMetadata: ...

    def discover_items(self) -> list[ItemDescriptor]: ...

    def open_item(self, item_id: str) -> OpenedItem: ...

    def refresh_item(self, item_id: str) -> RefreshResult: ...
