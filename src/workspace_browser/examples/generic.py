from __future__ import annotations

from datetime import datetime, timezone

from workspace_browser.core.models import ItemDescriptor, RefreshResult, WorkspaceMetadata
from workspace_browser.core.page import OpenedItem, PageDefinition, ViewSpec
from workspace_browser.core.status import ItemStatus
from workspace_browser.core.layout import container, view_slot


class GenericExampleWorkspace:
    @property
    def metadata(self) -> WorkspaceMetadata:
        return WorkspaceMetadata(
            identifier="generic-example",
            display_name="Generic Example Workspace",
            description="Example workspace that demonstrates discovery and viewing.",
            version="0.1.0",
            category="examples",
            tags=("example",),
        )

    def discover_items(self) -> list[ItemDescriptor]:
        now = datetime.now(tz=timezone.utc)
        return [
            ItemDescriptor(
                identifier="item-1",
                title="Example Item",
                subtitle="Demonstrates workspace item rendering",
                status=ItemStatus.READY,
                source_reference="memory://example/item-1",
                timestamp=now,
                tags=("example", "ready"),
                searchable_text="demo item",
            )
        ]

    def open_item(self, item_id: str) -> OpenedItem:
        item = self.discover_items()[0]
        page = PageDefinition(
            title=item.title,
            subtitle=item.subtitle,
            status=item.status.value,
            views=(
                ViewSpec(name="summary", callback=lambda _: "# Example Item\nThis is a markdown view."),
            ),
            layout=container("tabs", (view_slot("summary"),)),
        )
        page.validate()
        return OpenedItem(item=item, page=page)

    def refresh_item(self, item_id: str) -> RefreshResult:
        return RefreshResult(changed=False)
