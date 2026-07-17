from __future__ import annotations

from collections import defaultdict

from workspace_browser.core.models import ItemDescriptor


def search_items(items: list[ItemDescriptor], query: str) -> list[ItemDescriptor]:
    if not query:
        return items
    needle = query.lower()
    return [
        item
        for item in items
        if needle in item.title.lower()
        or (item.subtitle and needle in item.subtitle.lower())
        or (item.searchable_text and needle in item.searchable_text.lower())
    ]


def filter_items(
    items: list[ItemDescriptor],
    tags: set[str] | None = None,
) -> list[ItemDescriptor]:
    tag_filter = tags or set()
    filtered = items
    if tag_filter:
        filtered = [item for item in filtered if tag_filter.intersection(item.tags)]
    return filtered


def sort_items(items: list[ItemDescriptor], by: str = "title", descending: bool = False) -> list[ItemDescriptor]:
    if by == "timestamp":
        return sorted(
            items,
            key=lambda item: (
                item.timestamp is None,
                item.timestamp.timestamp() if item.timestamp else 0.0,
            ),
            reverse=descending,
        )
    return sorted(items, key=lambda item: item.title.lower(), reverse=descending)


def group_items(items: list[ItemDescriptor], field_name: str) -> dict[str, list[ItemDescriptor]]:
    grouped: dict[str, list[ItemDescriptor]] = defaultdict(list)
    for item in items:
        grouped[item.grouping_values.get(field_name, "ungrouped")].append(item)
    return dict(grouped)


def paginate_items(items: list[ItemDescriptor], page: int = 1, page_size: int = 50) -> list[ItemDescriptor]:
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]
