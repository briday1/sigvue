from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock

from workspace_browser.core.models import RefreshResult


@dataclass
class RefreshState:
    generation: int = 0
    running: bool = False
    last_success_at: datetime | None = None
    last_error: str | None = None


@dataclass
class RefreshManager:
    min_interval_seconds: float = 0.5
    _state_by_item: dict[str, RefreshState] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def _state(self, item_id: str) -> RefreshState:
        with self._lock:
            return self._state_by_item.setdefault(item_id, RefreshState())

    def begin_refresh(self, item_id: str) -> int | None:
        state = self._state(item_id)
        with self._lock:
            if state.running:
                return None
            state.running = True
            state.generation += 1
            return state.generation

    def complete_refresh(
        self,
        item_id: str,
        *,
        generation: int,
        result: RefreshResult,
    ) -> bool:
        state = self._state(item_id)
        with self._lock:
            if generation != state.generation:
                return False
            state.running = False
            if result.failed:
                state.last_error = "refresh_failed"
            else:
                state.last_error = None
                state.last_success_at = datetime.now(tz=timezone.utc)
            return True

    def fail_refresh(self, item_id: str, *, generation: int, error: Exception) -> bool:
        state = self._state(item_id)
        with self._lock:
            if generation != state.generation:
                return False
            state.running = False
            state.last_error = str(error)
            return True
