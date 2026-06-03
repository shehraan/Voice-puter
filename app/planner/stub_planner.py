"""Deterministic stub planner.

Used by tests and offline runs (no Ollama needed). It turns a goal hint into a fixed,
schema-valid plan. It is intentionally simple: the live driver is the Ollama planner.
"""
from __future__ import annotations

from app.planner.base import PlannerContext
from app.planner.schema import Action, ActionOp, GoalType, Plan, Postcondition, Target


def _ensure(app: str | None) -> Action:
    return Action(op=ActionOp.ensure_window, args={"app_name": app} if app else {})


class StubPlanner:
    def plan(self, context: PlannerContext) -> Plan:
        hint = context.goal_hint
        app = hint.target_app
        goal = hint.goal

        if goal in (GoalType.open_app, GoalType.focus_app):
            return Plan(
                goal=goal,
                target_app=app,
                confidence=0.95,
                rationale_short="resolve and focus the target app",
                actions=[_ensure(app)],
                postconditions=[Postcondition(type="control_exists", description="window is present", args={})],
            )

        if goal == GoalType.generic_text_entry:
            text = hint.payload or ""
            return Plan(
                goal=goal,
                target_app=app,
                confidence=0.9,
                rationale_short="open the app and type into its editable area",
                actions=[
                    _ensure(app),
                    Action(op=ActionOp.find_control, target=Target(semantic_role="editable_document")),
                    Action(op=ActionOp.type_text, target=Target(semantic_role="editable_document"),
                           args={"text": text, "clear_first": False}),
                ],
                postconditions=[Postcondition(type="visible_text_contains", args={"contains_any": [text]})],
            )

        if goal == GoalType.generic_search:
            query = hint.payload or ""
            return Plan(
                goal=goal,
                target_app=app,
                confidence=0.85,
                rationale_short="find the search field, type the query, open the best result",
                actions=[
                    _ensure(app),
                    Action(op=ActionOp.find_control, target=Target(semantic_role="search_or_input")),
                    Action(op=ActionOp.type_text, target=Target(semantic_role="search_or_input"),
                           args={"text": query, "clear_first": True}),
                    Action(op=ActionOp.send_hotkey, args={"keys": "enter"}),
                ],
                postconditions=[Postcondition(type="results_appeared", args={"contains_any": query.split()})],
            )

        if goal == GoalType.generic_click_named_control:
            name = hint.query or ""
            return Plan(
                goal=goal,
                target_app=app,
                confidence=0.8,
                rationale_short="find and invoke the named control",
                actions=[
                    Action(op=ActionOp.find_control, target=Target(semantic_role="named_control"),
                           args={"name_contains_any": [name]}),
                    Action(op=ActionOp.invoke_control, target=Target(semantic_role="named_control"),
                           args={"name_contains_any": [name]}),
                ],
                postconditions=[Postcondition(type="visible_state_changed", args={})],
            )

        if goal == GoalType.generic_build_or_run:
            return Plan(
                goal=goal,
                target_app=app,
                confidence=0.7,
                rationale_short="open the IDE and invoke a visible build/run control",
                actions=[
                    _ensure(app),
                    Action(op=ActionOp.find_control, target=Target(semantic_role="build_or_run")),
                    Action(op=ActionOp.invoke_control, target=Target(semantic_role="build_or_run")),
                ],
                postconditions=[Postcondition(type="visible_state_changed", args={"contains_any": ["build", "run", "terminal", "output"]})],
            )

        if goal == GoalType.no_op:
            return Plan(goal=GoalType.no_op, confidence=1.0, rationale_short="nothing to do")

        return Plan(
            goal=GoalType.clarify,
            target_app=app,
            confidence=0.3,
            rationale_short="command is ambiguous",
            actions=[Action(op=ActionOp.clarify, args={"message": "Please specify the app and the exact action."})],
        )
