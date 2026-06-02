"""Tests for planner schema validation and coercion."""
import pytest
from pydantic import ValidationError

from app.planner.schema import Action, ActionOp, GoalType, Plan, Postcondition, Target


def test_valid_plan_parses():
    data = {
        "goal": "generic_text_entry",
        "target_app": "notepad",
        "visual_demo_mode": True,
        "confidence": 0.9,
        "needs_confirmation": False,
        "rationale_short": "type into notepad",
        "actions": [
            {
                "op": "ensure_window",
                "target": {"selector_id": None, "semantic_role": None},
                "args": {"app_name": "notepad"},
            },
            {
                "op": "type_text",
                "target": {"selector_id": None, "semantic_role": "editable_document"},
                "args": {"text": "hello world", "clear_first": False},
            },
        ],
        "postconditions": [
            {"type": "visible_text_contains", "description": "text visible", "args": {"contains_any": ["hello world"]}},
        ],
    }
    plan = Plan.model_validate(data)
    assert plan.goal == GoalType.generic_text_entry
    assert len(plan.actions) == 2
    assert plan.actions[0].op == ActionOp.ensure_window
    assert plan.postconditions[0].type == "visible_text_contains"


def test_invalid_goal_raises():
    with pytest.raises(ValidationError):
        Plan.model_validate({"goal": "do_the_impossible", "actions": []})


def test_null_target_coerced_to_default():
    action = Action.model_validate({"op": "find_control", "target": None, "args": None})
    assert action.target == Target()
    assert action.args == {}


def test_null_actions_coerced():
    plan = Plan.model_validate({"goal": "no_op", "actions": None, "postconditions": None})
    assert plan.actions == []
    assert plan.postconditions == []


def test_all_goal_types_valid():
    for g in GoalType:
        plan = Plan(goal=g)
        assert plan.goal == g


def test_all_action_ops_valid():
    for op in ActionOp:
        action = Action(op=op)
        assert action.op == op


def test_plan_defaults():
    plan = Plan(goal=GoalType.no_op)
    assert plan.visual_demo_mode is True
    assert plan.needs_confirmation is False
    assert plan.confidence == 0.0
