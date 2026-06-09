"""Cloud LLM planner backed by the OpenAI Responses API.

Uses GPT-5.x reasoning models (default gpt-5.4 with high reasoning effort) so you can
compare planner quality against the local Ollama model. Requires OPENAI_API_KEY.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import PlannerConfig
from app.planner.base import PlannerContext
from app.planner.ollama_planner import PlannerUnavailable
from app.planner.prompt import build_messages
from app.planner.schema import GoalType, Plan


def _extract_output_text(data: dict[str, Any]) -> str:
    if text := data.get("output_text"):
        return str(text)
    chunks: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and part.get("text"):
                chunks.append(str(part["text"]))
    return "".join(chunks)


def _split_messages(messages: list[dict[str, str]]) -> tuple[str | None, str | list[dict[str, str]]]:
    instructions: str | None = None
    input_items: list[dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "system" and instructions is None:
            instructions = msg["content"]
            continue
        input_items.append({"role": msg["role"], "content": msg["content"]})
    if len(input_items) == 1 and input_items[0]["role"] == "user":
        return instructions, input_items[0]["content"]
    return instructions, input_items


class OpenAIPlanner:
    def __init__(self, cfg: PlannerConfig):
        self.cfg = cfg
        if not cfg.openai_api_key:
            raise PlannerUnavailable(
                "OPENAI_API_KEY is not set. Add it to .env or your environment, or use the local Ollama planner."
            )

    def _responses(self, messages: list[dict[str, str]]) -> str:
        instructions, input_payload = _split_messages(messages)
        url = f"{self.cfg.openai_base_url.rstrip('/')}/responses"
        body: dict[str, Any] = {
            "model": self.cfg.openai_model,
            "reasoning": {"effort": self.cfg.openai_reasoning_effort},
            "input": input_payload,
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": self.cfg.openai_max_output_tokens,
        }
        if instructions:
            body["instructions"] = instructions
        headers = {
            "Authorization": f"Bearer {self.cfg.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=self.cfg.openai_request_timeout_s)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise PlannerUnavailable(
                f"OpenAI request failed ({exc}). Check OPENAI_API_KEY and model "
                f"{self.cfg.openai_model!r}."
            ) from exc
        data = resp.json()
        if data.get("status") == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
            raise PlannerUnavailable(
                f"OpenAI response incomplete ({reason}). Try raising AGENT_OPENAI_MAX_OUTPUT_TOKENS."
            )
        content = _extract_output_text(data).strip()
        if not content:
            raise PlannerUnavailable("OpenAI returned no text output (check max_output_tokens).")
        return content

    @staticmethod
    def _parse(content: str) -> Plan:
        data = json.loads(content)
        return Plan.model_validate(data)

    def plan(self, context: PlannerContext) -> Plan:
        messages = build_messages(context)
        content = self._responses(messages)
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
            content = self._responses(messages)
            try:
                return self._parse(content)
            except (json.JSONDecodeError, ValidationError) as exc2:
                return Plan(
                    goal=GoalType.clarify,
                    rationale_short=f"planner produced invalid JSON: {exc2}",
                    confidence=0.0,
                )
