from __future__ import annotations

from enum import StrEnum


class ItemStatus(StrEnum):
    LIVE = "live"
    READY = "ready"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


def normalize_status(value: str | ItemStatus) -> ItemStatus:
    if isinstance(value, ItemStatus):
        return value
    try:
        return ItemStatus(str(value).lower())
    except ValueError:
        return ItemStatus.UNKNOWN
