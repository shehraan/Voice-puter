"""Planner output schema and the constrained action/goal vocabularies.

The planner is non-deterministic but its output is strictly validated here. Anything
the planner emits that is not in these vocabularies is rejected before it can reach the
executor (see security.md / planner.md).
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class GoalType(str, Enum):
    open_app = "open_app"
    focus_app = "focus_app"
    generic_search = "generic_search"
    generic_text_entry = "generic_text_entry"
    generic_select_result = "generic_select_result"
    generic_click_named_control = "generic_click_named_control"
    generic_form_create = "generic_form_create"
    generic_form_fill = "generic_form_fill"
    generic_form_submit = "generic_form_submit"
    generic_build_or_run = "generic_build_or_run"
    generic_open_project_or_file = "generic_open_project_or_file"
    generic_read_visible_state = "generic_read_visible_state"
    clarify = "clarify"
    no_op = "no_op"
    unsupported = "unsupported"


class ActionOp(str, Enum):
    ensure_window = "ensure_window"
    focus_window = "focus_window"
    observe_window = "observe_window"
    find_control = "find_control"
    focus_control = "focus_control"
    invoke_control = "invoke_control"
    set_value = "set_value"
    type_text = "type_text"
    send_hotkey = "send_hotkey"
    select_item = "select_item"
    double_click_element = "double_click_element"
    wait_for = "wait_for"
    verify = "verify"
    cache_selector = "cache_selector"
    repair_selector = "repair_selector"
    clarify = "clarify"
    stop_with_failure = "stop_with_failure"


class Target(BaseModel):
    selector_id: str | None = None
    semantic_role: str | None = None


class Action(BaseModel):
    op: ActionOp
    target: Target = Field(default_factory=Target)
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target", mode="before")
    @classmethod
    def _coerce_target(cls, v):
        return Target() if v is None else v

    @field_validator("args", mode="before")
    @classmethod
    def _coerce_args(cls, v):
        return {} if v is None else v


class Postcondition(BaseModel):
    type: str
    description: str = ""
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def _coerce_args(cls, v):
        return {} if v is None else v


class Plan(BaseModel):
    goal: GoalType
    target_app: str | None = None
    visual_demo_mode: bool = True
    confidence: float = 0.0
    needs_confirmation: bool = False
    rationale_short: str = ""
    actions: list[Action] = Field(default_factory=list)
    postconditions: list[Postcondition] = Field(default_factory=list)

    @field_validator("actions", "postconditions", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        return [] if v is None else v


# JSON skeleton handed to the planner model so it knows the exact shape to emit.
PLAN_JSON_SKELETON = {
    "goal": "one of the goal types",
    "target_app": "string|null",
    "visual_demo_mode": True,
    "confidence": 0.0,
    "needs_confirmation": False,
    "rationale_short": "string",
    "actions": [
        {
            "op": "one of the action ops",
            "target": {"selector_id": "string|null", "semantic_role": "string|null"},
            "args": {},
        }
    ],
    "postconditions": [{"type": "string", "description": "string", "args": {}}],
}

GOAL_TYPES = [g.value for g in GoalType]
ACTION_OPS = [a.value for a in ActionOp]
