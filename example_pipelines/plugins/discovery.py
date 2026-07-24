"""Reusable catalog schemas for common scientific recordings."""

from sigvue.plugin import DiscoveryColumn


SIGNAL_DISCOVERY_COLUMNS = (
    DiscoveryColumn("date", "Date", "datetime"),
    DiscoveryColumn("sample_rate", "Sampling rate", "si", unit="sample/s"),
    DiscoveryColumn("rf_frequency", "RF frequency", "si", unit="Hz"),
)

__all__ = ["SIGNAL_DISCOVERY_COLUMNS"]
