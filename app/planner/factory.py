"""Construct the active planner from configuration."""
from __future__ import annotations

from app.core.config import PlannerConfig
from app.planner.base import Planner
from app.planner.consensus_planner import ConsensusPlanner
from app.planner.ollama_planner import OllamaPlanner
from app.planner.openai_planner import OpenAIPlanner
from app.planner.stub_planner import StubPlanner


def make_planner(cfg: PlannerConfig, *, stub: bool = False, provider: str | None = None) -> Planner:
    if stub:
        return StubPlanner()
    active = (provider or cfg.provider).strip().lower()
    if active == "openai":
        base: Planner = OpenAIPlanner(cfg)
    elif active not in ("ollama", ""):
        raise ValueError(f"Unknown planner provider {active!r}; use 'ollama' or 'openai'.")
    else:
        base = OllamaPlanner(cfg)
    if cfg.consensus_enabled and cfg.consensus_agents > 1:
        return ConsensusPlanner(
            base,
            agents=cfg.consensus_agents,
            min_votes=cfg.consensus_min_votes,
            min_confidence=cfg.consensus_min_confidence,
        )
    return base