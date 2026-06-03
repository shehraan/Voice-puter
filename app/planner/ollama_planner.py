"""Local LLM planner backed by Ollama.

Calls a small local instruct model with JSON output mode, validates the result against
the strict Plan schema, and does a single constrained re-ask on parse failure before
falling back to a clarify plan. Fails loudly if Ollama is unreachable.
"""
from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from app.core.config import PlannerConfig
from app.planner.base import PlannerContext
from app.planner.prompt import build_messages
from app.planner.schema import GoalType, Plan


class PlannerUnavailable(RuntimeError):
    pass


class OllamaPlanner:
    def __init__(self, cfg: PlannerConfig):
        self.cfg = cfg

    def _chat(self, messages: list[dict[str, str]]) -> str:
        url = f"{self.cfg.base_url.rstrip('/')}/api/chat"
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.cfg.temperature},
        }
        try:
            resp = httpx.post(url, json=body, timeout=self.cfg.request_timeout_s)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise PlannerUnavailable(
                f"Ollama request failed ({exc}). Is `ollama serve` running and model "
                f"{self.cfg.model!r} pulled?"
            ) from exc
        return resp.json().get("message", {}).get("content", "")

    @staticmethod
    def _parse(content: str) -> Plan:
        data = json.loads(content)
        return Plan.model_validate(data)

    def plan(self, context: PlannerContext) -> Plan:
        messages = build_messages(context)
        content = self._chat(messages)
        try:
            return self._parse(content)
        except (json.JSONDecodeError, ValidationError) as exc:
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That was not valid. Error: {exc}. "
                        "Reply with ONLY a single valid JSON object matching the schema."
                    ),
                }
            )
            content = self._chat(messages)
            try:
                return self._parse(content)
            except (json.JSONDecodeError, ValidationError) as exc2:
                return Plan(
                    goal=GoalType.clarify,
                    rationale_short=f"planner produced invalid JSON: {exc2}",
                    confidence=0.0,
                )
