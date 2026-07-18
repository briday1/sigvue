from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points

from sigvue.core.contracts import Workspace
from sigvue.core.errors import WorkspaceLoadError


ENTRY_POINT_GROUP = "sigvue.workspaces"


@dataclass(frozen=True)
class DiscoveryFailure:
    package: str | None
    entry_point: str
    error_summary: str
    traceback: str | None = None


def discover_workspaces(group: str = ENTRY_POINT_GROUP) -> tuple[list[Workspace], list[DiscoveryFailure]]:
    eps = entry_points()
    selected = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])
    loaded: list[Workspace] = []
    failures: list[DiscoveryFailure] = []
    for ep in selected:
        _load_entrypoint(ep, loaded, failures)
    return loaded, failures


def _load_entrypoint(ep: EntryPoint, loaded: list[Workspace], failures: list[DiscoveryFailure]) -> None:
    try:
        obj = ep.load()
        workspace = obj() if callable(obj) else obj
        loaded.append(workspace)
    except Exception as exc:  # pragma: no cover - broad by design for isolation
        failures.append(
            DiscoveryFailure(
                package=ep.dist.name if ep.dist else None,
                entry_point=ep.name,
                error_summary=str(exc),
                traceback=repr(exc),
            )
        )


def assert_no_failures(failures: list[DiscoveryFailure]) -> None:
    if failures:
        summary = "; ".join(f"{f.entry_point}: {f.error_summary}" for f in failures)
        raise WorkspaceLoadError(summary)
