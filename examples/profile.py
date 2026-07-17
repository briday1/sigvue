"""Profile-compatible factories for the external example workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .generic import GenericExampleWorkspace
from .pri import create_workspace as pri_workspace
from .sigmf import create_workspace as sigmf_workspace
from .sigmf_matplotlib import create_workspace as sigmf_matplotlib_workspace
from .lfm_collection import create_workspace as lfm_collection_workspace
from .lfm_full_recording import create_workspace as lfm_full_recording_workspace


def create_generic_workspace(config: dict[str, Any] | None = None) -> GenericExampleWorkspace:
    values = config or {}
    return GenericExampleWorkspace(
        identifier=str(values.get("id", "generic-example")),
        name=str(values.get("name", "Generic Example Workspace")),
    )


def create_sigmf_workspace(config: dict[str, Any] | None = None):
    values = config or {}
    return sigmf_workspace(
        _data_root(values),
        identifier=str(values.get("id", "sigmf-viewer")),
        name=str(values.get("name", "SigMF File Viewer")),
    )


def create_sigmf_matplotlib_workspace(config: dict[str, Any] | None = None):
    values = config or {}
    return sigmf_matplotlib_workspace(
        _data_root(values),
        identifier=str(values.get("id", "sigmf-matplotlib-viewer")),
        name=str(values.get("name", "SigMF Matplotlib Viewer")),
    )


def create_pri_workspace(config: dict[str, Any] | None = None):
    values = config or {}
    return pri_workspace(
        _data_root(values),
        identifier=str(values.get("id", "pri-waterfall")),
        name=str(values.get("name", "PRI Waterfall Analysis")),
    )


def create_lfm_collection_workspace(config: dict[str, Any] | None = None):
    values = config or {}
    return lfm_collection_workspace(
        _data_root(values),
        identifier=str(values.get("id", "lfm-collection")),
        name=str(values.get("name", "10 MHz LFM Collection")),
    )


def create_lfm_full_recording_workspace(config: dict[str, Any] | None = None):
    values = config or {}
    return lfm_full_recording_workspace(
        _data_root(values),
        identifier=str(values.get("id", "lfm-full-recording")),
        name=str(values.get("name", "10 MHz LFM Collection (Whole Recording)")),
    )


def _data_root(config: dict[str, Any]) -> Path | None:
    value = config.get("data_root")
    return Path(value) if value else None
