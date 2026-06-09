"""Per-command tracing.

Every command produces one JSON trace file capturing transcript, goal, observations,
control candidates, chosen actions, verification results, latency spans, and the final
result. Failures must be loud and inspectable (see CLAUDE.md / testing.md).

Set ``live=True`` (or pass ``--live`` to run_text) to mirror each event to stderr as it
happens.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import TRACES_DIR


def _now_ms() -> float:
    return time.time() * 1000.0


def _status(ok: Any) -> str:
    if ok is True:
        return "ok"
    if ok is False:
        return "FAIL"
    return str(ok)


def _truncate(text: str, limit: int = 100) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def format_live_event(event: dict[str, Any]) -> str:
    """One-line summary of a trace event for terminal live logging."""
    kind = event.get("kind", "?")
    if kind == "shortlist":
        parts = [f"goal={event.get('goal')}", f"app={event.get('target_app')!r}"]
        if event.get("payload"):
            parts.append(f"query={event.get('payload')!r}")
        if event.get("query"):
            parts.append(f"control={event.get('query')!r}")
        return "shortlist  " + " ".join(parts)
    if kind == "plan":
        actions = event.get("actions") or []
        action_s = ",".join(actions) if actions else "(none)"
        return (
            f"plan       iter={event.get('iteration')} goal={event.get('goal')} "
            f"actions=[{action_s}] conf={event.get('confidence')} — "
            f"{_truncate(event.get('rationale') or '', 90)}"
        )
    if kind == "action":
        status = _status(event.get("ok"))
        detail = _truncate(event.get("detail") or "", 90)
        extra = ""
        if event.get("selector_id"):
            extra = f" @{event.get('selector_id')}"
        return f"action     {event.get('op')} {status}{extra} — {detail}"
    if kind in ("activate_result", "navigate_to_chat", "send_message", "navigate_to_results"):
        status = _status(event.get("ok", True))
        detail = _truncate(event.get("detail") or "", 90)
        then = event.get("then")
        stage = event.get("stage")
        suffix = ""
        if then:
            suffix += f" then={then}"
        if stage is not None:
            suffix += f" stage={stage}"
        if event.get("message"):
            suffix += f" msg={event.get('message')!r}"
        return f"{kind:<11} {status}{suffix} — {detail}"
    if kind == "goal_check":
        return f"goal_check {_status(event.get('ok'))} — {_truncate(event.get('detail') or '', 90)}"
    if kind == "progress":
        return f"progress   {_truncate(event.get('note') or '', 110)}"
    if kind == "repair":
        return f"repair     remaining={event.get('remaining')} prev={_truncate(event.get('previous') or '', 80)}"
    if kind == "search_via_shortcut":
        return f"search     {_status(event.get('ok'))} — {_truncate(event.get('detail') or '', 90)}"
    if kind == "verify":
        results = event.get("results") or []
        bits = [f"{r.get('type')}={_status(r.get('ok'))}" for r in results]
        return "verify     " + (", ".join(bits) if bits else "(none)")
    if kind == "observe_error":
        return f"observe    FAIL — {_truncate(event.get('detail') or '', 90)}"
    # Generic fallback for any future event kinds.
    skip = {"t_ms", "kind"}
    bits = [f"{k}={v!r}" for k, v in event.items() if k not in skip]
    return f"{kind:<11} " + " ".join(bits)


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
    live: bool = False

    def _emit_live(self, message: str) -> None:
        if not self.live:
            return
        t_ms = round(_now_ms() - self.started_at_ms, 1)
        print(f"[{t_ms:8.1f}ms] {message}", file=sys.stderr, flush=True)

    def log(self, kind: str, **payload: Any) -> None:
        event = {"t_ms": round(_now_ms() - self.started_at_ms, 1), "kind": kind, **payload}
        self.events.append(event)
        self._emit_live(format_live_event(event))

    @contextmanager
    def span(self, name: str):
        if self.live:
            self._emit_live(f">> {name} …")
        start = _now_ms()
        try:
            yield
        finally:
            elapsed_ms = _now_ms() - start
            self.spans[name] = round(self.spans.get(name, 0.0) + elapsed_ms, 1)
            if self.live:
                self._emit_live(f"<< {name} ({elapsed_ms / 1000:.1f}s)")

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
