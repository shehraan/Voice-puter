"""Planner interface and the context object handed to every planner."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.nlp.goal_shortlist import GoalHint
from app.planner.schema import Plan


@dataclass
class PlannerContext:
    transcript: str
    normalized: str
    goal_hint: GoalHint
    visual_demo_mode: bool = True
    window: dict[str, Any] | None = None  # current window summary
    observation: dict[str, Any] | None = None  # compact actionable controls
    cache_candidates: list[dict[str, Any]] = field(default_factory=list)
    previous_result: str | None = None
    memory: dict[str, Any] = field(default_factory=dict)
    consensus_agent_index: int | None = None
    consensus_agent_count: int | None = None


class Planner(Protocol):
    def plan(self, context: PlannerContext) -> Plan:  # pragma: no cover - interface
        ...
