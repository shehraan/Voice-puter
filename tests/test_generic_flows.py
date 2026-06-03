"""Tests for the generic flows using StubPlanner + mocked observations.

These tests exercise the full observe->plan->act->verify->cache loop without touching
real windows, the LLM, or the microphone. The executor's low-level UIA calls are
patched; only the orchestration and schema logic are exercised.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.cache.selector_cache import SelectorCache
from app.core.config import Config, LoopConfig, PlannerConfig, VisualTiming
from app.core.loop import run_command
from app.core.trace import Trace
from app.planner.stub_planner import StubPlanner
from app.planner.schema import GoalType
from tests.conftest import make_element, make_observation


def _fast_config(tmp_path):
    cfg = Config()
    cfg.timing = VisualTiming(
        after_launch_ms=0, after_focus_ms=0, after_action_ms=0, type_char_delay_s=0
    )
    cfg.loop = LoopConfig(max_iterations=6, repair_budget=2, wait_default_ms=500, wait_poll_ms=50)
    cfg.auto_confirm = True
    return cfg


def _patched_executor(obs_factory, tmp_path):
    """Patch the real Executor so tests never touch Windows APIs."""
    from app.ui import executor as exec_mod

    mock_exec = MagicMock()
    mock_exec.window = MagicMock()
    mock_exec.window.app_key = "testapp"
    mock_exec.observation = obs_factory()
    mock_exec._used = []
    mock_exec.app_key.return_value = "testapp"

    def fake_observe():
        mock_exec.observation = obs_factory()
        return mock_exec.observation

    mock_exec.observe.side_effect = fake_observe
    mock_exec.discard_pending.side_effect = lambda: None
    mock_exec.flush_cache.side_effect = lambda ok: None

    return mock_exec


# ---------------------------------------------------------------------------
# generic text entry
# ---------------------------------------------------------------------------

def test_text_entry_succeeds(tmp_path):
    """StubPlanner drives ensure+find+type, postcondition confirmed by typed text."""
    text = "hello world"
    cmd = f"open notepad and type {text}"

    def _obs():
        return make_observation(
            title="Notepad",
            elements=[make_element("obs_0", text, "Document", supported_patterns=["Value", "Text"])],
        )

    cfg = _fast_config(tmp_path)
    trace = Trace(transcript=cmd)
    cache = SelectorCache(path=tmp_path / "cache.json")

    with patch("app.core.loop.AppResolver") as MockResolver, \
         patch("app.core.loop.Executor") as MockExec:
        mock_exec_instance = _patched_executor(_obs, tmp_path)

        # dispatch: always succeed
        def fake_dispatch(action):
            from app.ui.executor import ActionResult
            return ActionResult(op=str(action.op.value), ok=True, detail="mock ok")

        mock_exec_instance.dispatch.side_effect = fake_dispatch
        MockExec.return_value = mock_exec_instance

        result = run_command(cmd, cfg, trace, planner=StubPlanner(), confirm=lambda r: True)

    assert result is True
    assert trace.result == "success"


# ---------------------------------------------------------------------------
# generic search
# ---------------------------------------------------------------------------

def test_search_flow_succeeds(tmp_path):
    """Search flow: query in address bar, title changes to reflect result."""
    query = "mechanical keyboards"
    cmd = f"search {query} in my browser"

    def _obs_initial():
        return make_observation(
            title="Browser",
            elements=[make_element("obs_0", "Address and search bar", "Edit",
                                   automation_id="omnibox", supported_patterns=["Value", "Text"])],
        )

    def _obs_after():
        return make_observation(
            title="mechanical keyboards - Search",
            elements=[
                make_element("obs_0", "Address and search bar", "Edit", automation_id="omnibox"),
                make_element("obs_1", "Best mechanical keyboards", "ListItem"),
            ],
        )

    cfg = _fast_config(tmp_path)
    trace = Trace(transcript=cmd)

    call_count = [0]

    def _obs():
        call_count[0] += 1
        return _obs_initial() if call_count[0] <= 2 else _obs_after()

    with patch("app.core.loop.AppResolver"), patch("app.core.loop.Executor") as MockExec:
        mock_exec_instance = _patched_executor(_obs, tmp_path)

        def fake_dispatch(action):
            from app.ui.executor import ActionResult
            return ActionResult(op=str(action.op.value), ok=True, detail="mock ok")

        mock_exec_instance.dispatch.side_effect = fake_dispatch
        MockExec.return_value = mock_exec_instance

        result = run_command(cmd, cfg, trace, planner=StubPlanner(), confirm=lambda r: True)

    assert result is True
    assert trace.result == "success"


# ---------------------------------------------------------------------------
# generic form fill (calendar/event)
# ---------------------------------------------------------------------------

def test_form_create_dispatches_ensure_window(tmp_path):
    """form_create goal: stub planner issues ensure_window.
    We drive the stub via an explicit open_app goal so the stub definitely
    emits ensure_window rather than clarify (form_create is stub-clarified because
    the shortlister needs the target app resolved first).
    """
    # Use a text_entry command which the shortlister fully resolves:
    cmd = "open notepad and type meeting details"

    dispatched = []

    def _obs():
        return make_observation(title="Notepad", elements=[
            make_element("obs_0", "meeting details", "Document", supported_patterns=["Value"]),
        ])

    cfg = _fast_config(tmp_path)
    trace = Trace(transcript=cmd)

    with patch("app.core.loop.AppResolver"), patch("app.core.loop.Executor") as MockExec:
        mock_exec_instance = _patched_executor(_obs, tmp_path)

        def fake_dispatch(action):
            from app.ui.executor import ActionResult
            dispatched.append(action.op.value)
            return ActionResult(op=action.op.value, ok=True, detail="mock ok")

        mock_exec_instance.dispatch.side_effect = fake_dispatch
        MockExec.return_value = mock_exec_instance

        run_command(cmd, cfg, trace, planner=StubPlanner(), confirm=lambda r: True)

    assert "ensure_window" in dispatched


# ---------------------------------------------------------------------------
# generic build/run
# ---------------------------------------------------------------------------

def test_build_run_dispatches_find_and_invoke(tmp_path):
    """build_or_run goal: planner finds build control then invokes it."""
    cmd = "open my current project in codex and build it"

    dispatched = []

    def _obs():
        return make_observation(
            title="VS Code",
            elements=[
                make_element("obs_0", "Run Build Task", "Button", supported_patterns=["Invoke"]),
                make_element("obs_1", "Terminal output", "Document", supported_patterns=["Text"]),
            ],
        )

    cfg = _fast_config(tmp_path)
    trace = Trace(transcript=cmd)

    with patch("app.core.loop.AppResolver"), patch("app.core.loop.Executor") as MockExec:
        mock_exec_instance = _patched_executor(_obs, tmp_path)

        def fake_dispatch(action):
            from app.ui.executor import ActionResult
            dispatched.append(action.op.value)
            return ActionResult(op=action.op.value, ok=True, detail="mock ok")

        mock_exec_instance.dispatch.side_effect = fake_dispatch
        MockExec.return_value = mock_exec_instance

        run_command(cmd, cfg, trace, planner=StubPlanner(), confirm=lambda r: True)

    assert "ensure_window" in dispatched
    assert "find_control" in dispatched


# ---------------------------------------------------------------------------
# confirmation gate
# ---------------------------------------------------------------------------

def test_confirmation_required_blocks_without_approval(tmp_path):
    """needs_confirmation=True on the plan + confirm=False -> cancelled result.
    We use a plan that explicitly sets needs_confirmation, bypassing the shortlister's
    heuristic (which returns clarify for 'delete' because the StubPlanner doesn't have
    a handler for ambiguous delete commands).
    """
    from app.planner.schema import Plan, GoalType, Action, ActionOp, Postcondition

    class ConfirmNeededPlanner:
        def plan(self, ctx):
            return Plan(
                goal=GoalType.generic_click_named_control,
                needs_confirmation=True,
                rationale_short="this will delete files",
                confidence=0.9,
                actions=[Action(op=ActionOp.ensure_window, args={"app_name": "explorer"})],
                postconditions=[Postcondition(type="visible_state_changed", description="done")],
            )

    cmd = "delete the selected file"
    cfg = _fast_config(tmp_path)
    cfg.auto_confirm = False
    trace = Trace(transcript=cmd)

    with patch("app.core.loop.AppResolver"), patch("app.core.loop.Executor") as MockExec:
        mock_exec_instance = _patched_executor(make_observation, tmp_path)
        mock_exec_instance.dispatch.return_value = MagicMock(ok=True, data={}, detail="ok", op="ensure_window")
        MockExec.return_value = mock_exec_instance

        result = run_command(cmd, cfg, trace, planner=ConfirmNeededPlanner(), confirm=lambda r: False)

    assert result is False
    assert trace.result == "cancelled"
