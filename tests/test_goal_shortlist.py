"""Tests for goal shortlist heuristics."""
import pytest

from app.nlp.goal_shortlist import GoalHint, shortlist
from app.planner.schema import GoalType


@pytest.mark.parametrize("cmd,expected_goal,expected_app,expected_payload", [
    (
        "open notepad and type hello world",
        GoalType.generic_text_entry,
        "notepad",
        "hello world",
    ),
    (
        "search janice stfu in my music app and play the best result",
        GoalType.generic_search,
        "my music app",
        "janice stfu",
    ),
    (
        "search nearby repair shops in my browser",
        GoalType.generic_search,
        "my browser",
        "nearby repair shops",
    ),
    (
        "open my current project in codex and build it",
        GoalType.generic_build_or_run,
        "codex",
        None,
    ),
    (
        "open calculator",
        GoalType.open_app,
        "calculator",
        None,
    ),
    (
        "type hello world",
        GoalType.generic_text_entry,
        None,
        "hello world",
    ),
    (
        "click the save button",
        GoalType.generic_click_named_control,
        None,
        None,
    ),
    (
        "",
        GoalType.no_op,
        None,
        None,
    ),
])
def test_shortlist(cmd, expected_goal, expected_app, expected_payload):
    hint: GoalHint = shortlist(cmd)
    assert hint.goal == expected_goal
    if expected_app is not None:
        assert hint.target_app is not None
        assert expected_app.lower() in hint.target_app.lower() or hint.target_app.lower() in expected_app.lower()
    if expected_payload is not None:
        assert hint.payload is not None
        assert expected_payload.lower() in hint.payload.lower()
