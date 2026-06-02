"""Per-command tracing.

Every command produces one JSON trace file capturing transcript, goal, observations,
control candidates, chosen actions, verification results, latency spans, and the final
result. Failures must be loud and inspectable (see CLAUDE.md / testing.md).
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import TRACES_DIR


def _now_ms() -> float:
    return time.time() * 1000.0


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at_ms: float = field(default_factory=_now_ms)
    transcript: str = ""
    normalized: str = ""
    goal: str | None = None
    target_app: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    spans: dict[str, float] = field(default_factory=dict)
    result: str = "pending"
    failure_reason: str | None = None

    def log(self, kind: str, **payload: Any) -> None:
        self.events.append({"t_ms": round(_now_ms() - self.started_at_ms, 1), "kind": kind, **payload})

    @contextmanager
    def span(self, name: str):
        start = _now_ms()
        try:
            yield
        finally:
            self.spans[name] = round(self.spans.get(name, 0.0) + (_now_ms() - start), 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "transcript": self.transcript,
            "normalized": self.normalized,
            "goal": self.goal,
            "target_app": self.target_app,
            "result": self.result,
            "failure_reason": self.failure_reason,
            "total_ms": round(_now_ms() - self.started_at_ms, 1),
            "spans": self.spans,
            "events": self.events,
        }

    def save(self, directory: Path | None = None) -> Path:
        directory = directory or TRACES_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.trace_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path
