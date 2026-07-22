"""Memory-reporting helpers shared by example presentations."""


def format_bytes(byte_count: int) -> str:
    """Format an exact byte count with a compact binary unit."""
    value = float(max(0, byte_count))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024.0
    raise AssertionError("unreachable")
