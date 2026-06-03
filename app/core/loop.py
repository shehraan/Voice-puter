"""Observe -> plan -> act -> observe orchestrator.

Drives one command to completion: shortlist the goal, then repeatedly let the planner
choose 1-3 grounded actions, execute them visibly, re-observe, verify postconditions,
and cache or repair. Bounded by iteration and repair budgets. Never claims success
without a passing postcondition (architecture.md runtime loop).
"""
from __future__ import annotations

import time
from typing import Callable

from app.cache.selector_cache import SelectorCache
from app.core.config import Config
from app.core.trace import Trace
from app.launch.resolver import AppResolver
from app.memory.resolvers import memory_summary
from app.nlp.goal_shortlist import shortlist
from app.nlp.normalizer import normalize
from app.planner.base import Planner, PlannerContext
from app.planner.schema import Action, ActionOp, GoalType, Target
from app.safety import guardrails
from app.ui.executor import Executor
from app.verifier.verify import goal_satisfied, state_signature, verify_postcondition

ConfirmFn = Callable[[str], bool]


def _cache_candidates(cache: SelectorCache, app_key: str) -> list[dict]:
    out = []
    for role, raw in cache.data.get(app_key, {}).items():
        out.append(
            {
                "semantic_role": role,
                "automation_id": raw.get("automation_id"),
                "control_type": raw.get("control_type"),
                "degraded": raw.get("degraded", False),
            }
        )
    return out


def run_command(
    command: str,
    cfg: Config,
    trace: Trace,
    planner: Planner | None = None,
    confirm: ConfirmFn | None = None,
) -> bool:
    normalized = normalize(command)
    hint = shortlist(normalized)
    trace.normalized = normalized
    trace.goal = hint.goal.value
    trace.target_app = hint.target_app
    trace.log("shortlist", goal=hint.goal.value, target_app=hint.target_app, payload=hint.payload, query=hint.query)

    if planner is None:
        from app.planner.ollama_planner import OllamaPlanner

        planner = OllamaPlanner(cfg.planner)

    cache = SelectorCache()
    executor = Executor(cfg, cache, trace, AppResolver())

    cmd_needs_conf, conf_reason = guardrails.command_needs_confirmation(normalized)
    confirmed = False
    prev_result: str | None = None
    repair_budget = cfg.loop.repair_budget

    for iteration in range(cfg.loop.max_iterations):
        obs = executor.observation
        ctx = PlannerContext(
            transcript=command,
            normalized=normalized,
            goal_hint=hint,
            visual_demo_mode=cfg.visual_demo_mode,
            window=obs.window.to_dict() if obs else None,
            observation=obs.to_compact() if obs else None,
            cache_candidates=_cache_candidates(cache, executor.app_key()),
            previous_result=prev_result,
            memory=memory_summary(),
        )

        with trace.span("plan"):
            plan = planner.plan(ctx)
        trace.log(
            "plan",
            iteration=iteration,
            goal=plan.goal.value,
            confidence=plan.confidence,
            needs_confirmation=plan.needs_confirmation,
            rationale=plan.rationale_short,
            actions=[a.op.value for a in plan.actions],
        )

        if plan.goal == GoalType.no_op:
            trace.result = "success"
            return True
        if plan.goal == GoalType.clarify:
            trace.result = "needs_clarification"
            trace.failure_reason = plan.rationale_short or "ambiguous command"
            print(f"clarify: {trace.failure_reason}")
            return False
        if plan.goal == GoalType.unsupported:
            trace.result = "unsupported"
            trace.failure_reason = plan.rationale_short or "unsupported command"
            return False

        if (plan.needs_confirmation or cmd_needs_conf) and not confirmed:
            reason = conf_reason or plan.rationale_short or "this action may be destructive"
            if cfg.auto_confirm:
                confirmed = True
            elif confirm and confirm(reason):
                confirmed = True
            else:
                trace.result = "cancelled"
                trace.failure_reason = f"confirmation required: {reason}"
                print(f"cancelled (needs confirmation): {reason}")
                return False

        # Safeguard: if no window is open yet and the planner did not start with
        # ensure_window, prepend one so grounded actions have a window to target.
        if executor.window is None and plan.actions and plan.actions[0].op != ActionOp.ensure_window:
            app = hint.target_app or plan.target_app
            if app:
                plan.actions.insert(0, Action(op=ActionOp.ensure_window, args={"app_name": app}))

        baseline = executor.observation
        all_ok = True
        typed_search = False
        for action in plan.actions:
            # Ground the target app from the shortlist when the planner omits it.
            if action.op == ActionOp.ensure_window and not action.args.get("app_name") and hint.target_app:
                action.args["app_name"] = hint.target_app
            # Ground the typed TEXT in the user's parsed command, not the model's guess.
            if action.op == ActionOp.type_text and hint.payload and hint.goal in (
                GoalType.generic_text_entry,
                GoalType.generic_search,
            ):
                action.args["text"] = hint.payload
                if hint.goal == GoalType.generic_search:
                    action.args.setdefault("clear_first", True)
            res = executor.dispatch(action)
            prev_result = f"{res.op}: {'ok' if res.ok else 'FAIL'} - {res.detail}"
            if action.op == ActionOp.type_text and res.ok and hint.goal == GoalType.generic_search:
                typed_search = True
            if action.op == ActionOp.send_hotkey:
                typed_search = False
            if res.data.get("clarify"):
                trace.result = "needs_clarification"
                trace.failure_reason = res.detail
                return False
            if res.data.get("stop"):
                all_ok = False
                break
            if not res.ok:
                all_ok = False
                break

        # A search must be submitted: auto-press Enter if the planner typed the query
        # but did not submit it this turn (generic search-and-go convention).
        if typed_search and all_ok:
            res = executor.dispatch(Action(op=ActionOp.send_hotkey, args={"keys": "enter"}))
            prev_result = f"{res.op}: {'ok' if res.ok else 'FAIL'} - {res.detail}"
            time.sleep(1.5)  # let results/navigation render before observing

        fresh = executor.observe() if executor.window else None

        # Authoritative, goal-derived completion check (independent of the planner's
        # self-reported postconditions, which a small model often makes too lenient).
        goal_check = goal_satisfied(hint, fresh, baseline)
        if goal_check is not None:
            done = goal_check.ok
            trace.log("goal_check", ok=done, detail=goal_check.detail)
        elif plan.postconditions and fresh is not None:
            checks = [(pc.type, verify_postcondition(pc, fresh, baseline)) for pc in plan.postconditions]
            done = all(c.ok for _, c in checks)
            trace.log("verify", results=[{"type": t, "ok": c.ok, "detail": c.detail} for t, c in checks])
        else:
            done = False

        if all_ok and done:
            executor.flush_cache(True)
            trace.result = "success"
            return True

        made_progress = all_ok and state_signature(fresh) != state_signature(baseline)
        if made_progress:
            executor.discard_pending()
            trace.log("progress", note=prev_result)
            continue

        executor.flush_cache(False)
        repair_budget -= 1
        trace.log("repair", remaining=repair_budget, previous=prev_result)
        if repair_budget < 0:
            trace.result = "failed"
            trace.failure_reason = prev_result or "goal not satisfied"
            return False

    trace.result = "failed"
    trace.failure_reason = "max iterations reached without verified success"
    return False
