"""Presentation-neutral formatting helpers."""

from __future__ import annotations

from typing import Any


def format_bytes(byte_count: int) -> str:
    """Format an exact byte count with a compact binary unit."""
    if isinstance(byte_count, bool) or not isinstance(byte_count, int):
        raise TypeError("byte_count must be an integer")
    value = float(max(0, byte_count))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024.0
    raise AssertionError("unreachable")


def resident_nbytes(*values: Any, deduplicate: bool = True) -> int:
    """Sum ``nbytes`` values, optionally counting shared objects only once."""
    seen: set[int] = set()
    total = 0
    for value in values:
        identifier = id(value)
        if deduplicate and identifier in seen:
            continue
        seen.add(identifier)
        byte_count = getattr(value, "nbytes", None)
        if isinstance(byte_count, bool) or not isinstance(byte_count, int):
            raise TypeError("resident_nbytes values must expose an integer nbytes")
        total += max(0, byte_count)
    return total
