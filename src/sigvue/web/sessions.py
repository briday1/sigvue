from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionStore:
    values: dict[str, dict[str, object]] = field(default_factory=dict)

    def get(self, session_id: str) -> dict[str, object]:
        return self.values.setdefault(session_id, {})
