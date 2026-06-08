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
from app.launch.app_index import search_plays_on_enter, search_shortcut
from app.launch.resolver import AppResolver
from app.memory.resolvers import memory_summary
from app.nlp.goal_shortlist import GoalHint, shortlist
from app.nlp.normalizer import normalize
from app.planner.base import Planner, PlannerContext
from app.planner.schema import Action, ActionOp, GoalType, Target
from app.safety import guardrails
from app.ui.elements import UIElement
from app.ui.executor import ActionResult, Executor
from app.verifier.verify import (
    VerifyResult,
    goal_satisfied,
    message_sent,
    playable_result_present,
    read_value,
    result_activated,
    results_ready_for_followup,
    search_results_page_visible,
    search_results_present,
    state_signature,
    verify_postcondition,
)

ConfirmFn = Callable[[str], bool]

# Poll interval / budget while waiting for real track rows after a nav-suggestion click.
_PLAYABLE_POLL_S = 0.5
_PLAYABLE_POLL_TRIES = 24  # up to ~12s (Navidrome results can be slow)
_LIVE_SEARCH_POLL_S = 0.4
_LIVE_SEARCH_POLL_TRIES = 24  # up to ~9.6s after typing for dropdown results
_NAV_SUGGESTION_POLL_S = 0.5
_NAV_SUGGESTION_POLL_TRIES = 16
_PLAY_ROW_POLL_S = 0.5
_PLAY_ROW_POLL_TRIES = 20


def _live_search_dropdown_visible(executor: Executor, payload: str | None) -> bool:
    """True when a live-search dropdown is on screen (not the full results page)."""
    obs = executor.observation
    if obs is None or not payload:
        return False
    if search_results_page_visible(obs, payload):
        return False
    return bool(executor._find_nav_suggestion(payload)) or search_results_present(obs, payload)


def _poll_live_search(executor: Executor, payload: str | None):
    """After typing, wait for live-search dropdown suggestions to appear."""
    obs = executor.observation
    for _ in range(_LIVE_SEARCH_POLL_TRIES):
        if _live_search_dropdown_visible(executor, payload):
            return obs
        time.sleep(_LIVE_SEARCH_POLL_S)
        obs = executor.observe()
    return obs


def _poll_playable(executor: Executor, payload: str | None) -> Observation | None:
    """Re-observe until playable content or the full results page appears, or timeout."""
    obs = executor.observation
    for _ in range(_PLAYABLE_POLL_TRIES):
        if playable_result_present(obs, payload) or search_results_page_visible(obs, payload):
            return obs
        if payload and executor._find_query_result_row(payload) is not None:
            return obs
        time.sleep(_PLAYABLE_POLL_S)
        obs = executor.observe()
    return obs


def _media_play_stages(
    executor: Executor,
    hint: GoalHint,
    trace: Trace,
    fresh,
    *,
    nav_done: bool,
) -> tuple[bool, bool, object, str | None, bool]:
    """Run nav-then-play. Returns (activated, nav_done, fresh, prev_result, results_ready)."""
    payload = hint.payload or ""
    then = hint.then or "play"
    activated = False
    results_ready = False
    prev_result: str | None = None

    if not search_results_present(fresh, payload) and not search_results_page_visible(fresh, payload):
        time.sleep(2.5)
        fresh = executor.observe()

    # Enter on some apps dismisses the dropdown; refocus search to revive suggestions.
    if not nav_done and not _live_search_dropdown_visible(executor, payload):
        if _header_search_contains(fresh, payload):
            sf = executor._find_search_field()
            if sf is not None:
                executor._focus_element(sf)
                for _ in range(8):
                    time.sleep(0.4)
                    fresh = executor.observe()
                    if _live_search_dropdown_visible(executor, payload):
                        break

    # Stage A: click live-search nav suggestion when present.
    if not nav_done and executor._find_nav_suggestion(payload):
        for _ in range(_NAV_SUGGESTION_POLL_TRIES):
            if executor._find_nav_suggestion(payload):
                break
            time.sleep(_NAV_SUGGESTION_POLL_S)
            fresh = executor.observe()
        if executor._find_nav_suggestion(payload):
            act = executor.activate_best_result(payload, "open")
        else:
            act = ActionResult("activate_result", False, f"no nav suggestion for {payload!r}")
        trace.log("activate_result", ok=act.ok, detail=act.detail, then=then, stage="nav")
        prev_result = f"activate_result: {'ok' if act.ok else 'FAIL'} - {act.detail}"
        if act.ok and act.data.get("is_nav_suggestion"):
            trace.log("navigate_to_results", detail=act.detail)
            nav_done = True
            fresh = _poll_playable(executor, payload)
            results_ready = True

    # Stage B: play the actual track row.
    if not results_ready_for_followup(fresh, payload):
        fresh = _poll_playable(executor, payload)
    still_dropdown = (
        not nav_done
        and search_results_present(fresh, payload)
        and not search_results_page_visible(fresh, payload)
        and executor._find_nav_suggestion(payload)
    )
    if (results_ready_for_followup(fresh, payload) or nav_done) and not still_dropdown:
        results_ready = True
        executor._refresh_chromium_observation()
        fresh = executor.observation
        polled: UIElement | None = None
        for _ in range(_PLAY_ROW_POLL_TRIES):
            if not nav_done and executor._find_nav_suggestion(payload):
                act = executor.activate_best_result(payload, "open")
                trace.log("activate_result", ok=act.ok, detail=act.detail, then=then, stage="nav")
                if act.ok and act.data.get("is_nav_suggestion"):
                    nav_done = True
                    trace.log("navigate_to_results", detail=act.detail)
                    time.sleep(1.0)
                    fresh = executor._refresh_chromium_observation()
                    continue
            polled = (
                executor.find_consensus_play_target(payload)
                or executor._find_query_result_row(payload)
                or executor._find_search_datagrid_row(payload)
            )
            if polled and not Executor._is_nav_suggestion((polled.name or "").lower()):
                executor._pending_play_target = polled
                break
            polled = None
            time.sleep(_PLAY_ROW_POLL_S)
            fresh = executor._refresh_chromium_observation()
        act = executor.activate_best_result(payload, then, exclude_nav=True, hint_el=polled)
        trace.log("activate_result", ok=act.ok, detail=act.detail, then=then, stage="play")
        prev_result = f"activate_result: {'ok' if act.ok else 'FAIL'} - {act.detail}"
        if act.ok and not act.data.get("is_nav_suggestion"):
            activated = True
            time.sleep(4.0)
            fresh = executor.observe()
        elif not act.ok:
            trace.log("play_failed", detail=act.detail)

    return activated, nav_done, fresh, prev_result, results_ready


def _header_search_contains(obs, payload: str) -> bool:
    """True when the named app Search field already holds the query."""
    from app.verifier.verify import _named_header_search_edits

    terms = [t for t in payload.lower().split() if len(t) > 1]
    if not terms:
        return False
    for e in _named_header_search_edits(obs):
        text = f"{e.name or ''} {read_value(e)}".strip().lower()
        if any(t in text for t in terms):
            return True
    return False


def _maybe_inline_search(
    executor: Executor,
    hint: GoalHint,
    trace: Trace,
) -> tuple[bool, str | None]:
    """Type the query into the app search field when the planner has not submitted yet."""
    if hint.goal != GoalType.generic_search or not hint.payload:
        return False, None
    obs = executor.observation
    if obs is None or executor.window is None:
        return False, None
    if _search_phase_complete(obs, hint.payload) or _header_search_contains(obs, hint.payload):
        return True, None
    sf = executor._find_search_field()
    if sf is None:
        return False, None
    executor._focus_element(sf)
    res = executor.dispatch(
        Action(
            op=ActionOp.type_text,
            target=Target(selector_id=str(sf.selector_id), semantic_role="search_or_input"),
            args={"text": hint.payload, "clear_first": True},
        )
    )
    trace.log(
        "action",
        op="type_text",
        ok=res.ok,
        detail=res.detail,
        selector_id=str(sf.selector_id),
    )
    prev = f"{res.op}: {'ok' if res.ok else 'FAIL'} - {res.detail}"
    if not res.ok:
        return False, prev
    executor.observation = _poll_live_search(executor, hint.payload)
    if hint.then:
        trace.log(
            "action",
            op="send_hotkey",
            ok=True,
            detail="skipped enter — inline nav/play handles search submit",
        )
        return True, prev
    if not _live_search_dropdown_visible(executor, hint.payload):
        sub = executor.dispatch(Action(op=ActionOp.send_hotkey, args={"keys": "enter"}))
        prev = f"{sub.op}: {'ok' if sub.ok else 'FAIL'} - {sub.detail}"
    return True, prev


def _search_phase_complete(obs, payload: str | None) -> bool:
    """True when a prior search already produced a follow-up-ready results view."""
    from app.verifier.verify import _on_search_route

    if obs is None or not payload:
        return False
    if not _on_search_route(obs):
        return False
    if not _header_search_contains(obs, payload):
        return False
    return search_results_page_visible(obs, payload) and playable_result_present(obs, payload)


def _skip_lyric_panel_invoke(action: Action, executor: Executor, hint: GoalHint) -> bool:
    """Skip planner invokes that open the lyrics-panel search instead of app search."""
    if hint.goal != GoalType.generic_search or action.op != ActionOp.invoke_control:
        return False
    el, _ = executor._resolve_target(action)
    if el is None:
        return False
    name = (el.name or "").strip().upper()
    return name in ("SEARCH", "CANCEL", "CLEAR", "EXPORT LYRICS") and el.rectangle[1] > 400


def _skip_search_action(
    action: Action,
    hint: GoalHint,
    *,
    searched: bool,
    nav_done: bool,
    results_ready: bool,
    activated: bool,
) -> bool:
    """True when the planner is re-issuing search steps that are already complete."""
    if not (hint.goal == GoalType.generic_search and hint.then and searched and not activated):
        return False
    if not (nav_done or results_ready):
        return False
    if action.op in (ActionOp.type_text, ActionOp.send_hotkey):
        return True
    if action.op in (ActionOp.find_control, ActionOp.focus_control):
        role = (action.target.semantic_role if action.target else None) or ""
        return role == "search_or_input"
    return False


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


def _search_with_shortcut(hint: GoalHint, cfg: Config, trace: Trace, executor: Executor) -> bool:
    """Deterministic search (+ optional play/open) for apps with a known search hotkey.

    Avoids the phantom Chromium omnibox by using the app's own search shortcut, then
    verifies the visible end state. Used for media apps like Spotify (Ctrl+K plays the
    top result on Enter).
    """
    app = hint.target_app or ""
    shortcut = search_shortcut(app)
    plays_on_enter = search_plays_on_enter(app)

    res = executor.dispatch(Action(op=ActionOp.ensure_window, args={"app_name": app}))
    if not res.ok:
        trace.result = "failed"
        trace.failure_reason = res.detail
        return False
    baseline = executor.observe()

    sres = executor.search_via_shortcut(shortcut, hint.payload or "")
    trace.log("search_via_shortcut", ok=sres.ok, detail=sres.detail)
    if not sres.ok:
        trace.result = "failed"
        trace.failure_reason = sres.detail
        return False

    # Submit the query.
    sub = executor.dispatch(Action(op=ActionOp.send_hotkey, args={"keys": "enter"}))
    if not sub.ok:
        trace.result = "failed"
        trace.failure_reason = sub.detail
        return False

    # Wait for search results to appear.
    time.sleep(1.5)
    fresh = executor.observe()
    if not search_results_present(fresh, hint.payload):
        time.sleep(1.5)  # extra wait on slow machines
        fresh = executor.observe()

    want = hint.then or ("play" if plays_on_enter else None)
    if want:
        # Always activate the best result explicitly; don't rely on Enter auto-playing.
        if search_results_present(fresh, hint.payload):
            act = executor.activate_best_result(hint.payload or "", want)
            trace.log("activate_result", ok=act.ok, detail=act.detail, then=want)
            # Wait for playback to actually start (now-playing bar update can lag ~1-2s).
            time.sleep(2.5)
            fresh = executor.observe()
        chk = result_activated(hint, fresh)
    else:
        ok = search_results_present(fresh, hint.payload)
        chk = VerifyResult(ok, "results reflect the query" if ok else "no results reflecting the query yet")

    trace.log("goal_check", ok=chk.ok, detail=chk.detail)
    if chk.ok:
        trace.result = "success"
        return True
    trace.result = "failed"
    trace.failure_reason = chk.detail
    return False


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
        from app.planner.factory import make_planner

        planner = make_planner(cfg.planner)

    cache = SelectorCache()
    executor = Executor(cfg, cache, trace, AppResolver())

    # Deterministic, grounded fast-path for apps with a known search hotkey (e.g.
    # Spotify): more reliable than letting the planner fight the phantom omnibox.
    if hint.goal == GoalType.generic_search and hint.target_app and search_shortcut(hint.target_app):
        return _search_with_shortcut(hint, cfg, trace, executor)

    cmd_needs_conf, conf_reason = guardrails.command_needs_confirmation(normalized)
    confirmed = False
    prev_result: str | None = None
    repair_budget = cfg.loop.repair_budget
    searched = False  # a search query has been typed+submitted
    results_ready = False  # results page visible; search phase is done
    nav_done = False  # dropdown "Search for X" already clicked
    activated = False  # the follow-up action (play/open/select) has run
    _last_activation_sig: tuple = ()  # UI state signature at the last activation

    for iteration in range(cfg.loop.max_iterations):
        play_attempted = False
        if not searched and executor.observation and _search_phase_complete(
            executor.observation, hint.payload,
        ):
            searched = True
            results_ready = True
        if not searched and executor.window:
            inline_searched, inline_prev = _maybe_inline_search(executor, hint, trace)
            if inline_prev:
                prev_result = inline_prev
            if inline_searched:
                searched = True
        # Detect results on screen and attempt play BEFORE the planner can re-search.
        if (hint.goal == GoalType.generic_search and hint.then == "play"
                and searched and not activated and executor.window):
            pre = executor.observe()
            results_ready = results_ready or results_ready_for_followup(pre, hint.payload)
            play_attempted = True
            activated, nav_done, pre, prev_result, results_ready = _media_play_stages(
                executor, hint, trace, pre, nav_done=nav_done,
            )
            executor.observation = pre
            if activated:
                chk = result_activated(hint, pre)
                for _ in range(10):
                    if chk.ok:
                        break
                    time.sleep(0.5)
                    pre = executor.observe()
                    chk = result_activated(hint, pre)
                trace.log("goal_check", ok=chk.ok, detail=chk.detail)
                if chk.ok:
                    executor.flush_cache(True)
                    trace.result = "success"
                    return True

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

        # For a search-and-(play/open/select) command, success is only ever granted by
        # the verified activation step below - never by the planner self-declaring no_op.
        pending_activation = hint.goal == GoalType.generic_search and bool(hint.then)
        if plan.goal == GoalType.no_op and not pending_activation:
            trace.result = "success"
            return True
        if plan.goal == GoalType.clarify:
            if pending_activation and not activated:
                trace.log(
                    "progress",
                    note=f"planner clarify ignored — search/play pending: {plan.rationale_short}",
                )
                repair_budget -= 1
                continue
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
        query_typed = False  # a search query was entered this turn (survives send_hotkey)
        live_results_visible = False  # set when typing reveals a live-search dropdown
        for action in plan.actions:
            # Search-and-play: once results are on screen, never re-type or re-submit.
            if _skip_search_action(
                action, hint,
                searched=searched, nav_done=nav_done,
                results_ready=results_ready, activated=activated,
            ):
                trace.log("action", op=action.op.value, ok=True,
                          detail="skipped — search already complete, proceeding to play")
                continue
            # Keep ensure_window grounded to the shortlist's target app.
            # If hint.target_app is known, override whatever the planner wrote so the
            # model cannot hallucinate a switch to a different app mid-task.
            if action.op == ActionOp.ensure_window and hint.target_app:
                action.args["app_name"] = hint.target_app
            # Ground the typed TEXT in the user's parsed command, not the model's guess.
            if _skip_lyric_panel_invoke(action, executor, hint):
                trace.log("action", op=action.op.value, ok=True,
                          detail="skipped — lyrics panel control not used for app search")
                continue
            if action.op == ActionOp.type_text and hint.payload and hint.goal in (
                GoalType.generic_text_entry,
                GoalType.generic_search,
            ):
                action.args["text"] = hint.payload
                if hint.goal == GoalType.generic_search:
                    action.args.setdefault("clear_first", True)
                    sf = executor._find_search_field()
                    if sf is not None:
                        executor._focus_element(sf)
                        action.target = Target(
                            selector_id=str(sf.selector_id),
                            semantic_role="search_or_input",
                        )
            # Skip Enter when live-search results are already visible (e.g. Feishin's
            # dropdown). Pressing Enter in those apps dismisses the dropdown rather than
            # navigating into results; clicking the suggestion ListItem is the right move.
            if (action.op == ActionOp.send_hotkey
                    and action.args.get("keys", "").lower() in ("enter", "return")
                    and hint.goal == GoalType.generic_search):
                executor.observe()
                skip_enter = (
                    live_results_visible
                    or _live_search_dropdown_visible(executor, hint.payload)
                    or (hint.then and (query_typed or typed_search))
                )
                if skip_enter:
                    trace.log("action", op="send_hotkey", ok=True,
                              detail="skipped enter — inline nav/play handles search submit")
                    searched = True
                    typed_search = False
                    live_results_visible = False
                    continue

            res = executor.dispatch(action)
            prev_result = f"{res.op}: {'ok' if res.ok else 'FAIL'} - {res.detail}"
            if action.op == ActionOp.type_text and res.ok and hint.goal == GoalType.generic_search:
                typed_search = True
                query_typed = True
                _peek = _poll_live_search(executor, hint.payload)
                executor.observation = _peek
                if _live_search_dropdown_visible(executor, hint.payload):
                    live_results_visible = True
            if action.op == ActionOp.send_hotkey and res.ok:
                if query_typed:
                    searched = True
                typed_search = False
                live_results_visible = False
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
        # but did not submit it this turn — unless live results already appeared.
        if typed_search and all_ok:
            if hint.then:
                searched = True
            elif not live_results_visible and not _live_search_dropdown_visible(executor, hint.payload):
                res = executor.dispatch(Action(op=ActionOp.send_hotkey, args={"keys": "enter"}))
                prev_result = f"{res.op}: {'ok' if res.ok else 'FAIL'} - {res.detail}"
                time.sleep(1.5)
            elif live_results_visible or _live_search_dropdown_visible(executor, hint.payload):
                trace.log("action", op="send_hotkey", ok=True,
                          detail="skipped enter — live search results already visible")
            searched = True
        elif query_typed and all_ok:
            searched = True

        fresh = executor.observe() if executor.window else None

        # --- search -> select/play/send_message continuation ----------------
        # Deterministic follow-up after search results appear: select a contact and
        # send a message, or play/open/select a media result.
        if hint.goal == GoalType.generic_search and hint.then:
            if hint.then == "send_message":
                # ── Messaging path ────────────────────────────────────────────
                # Step 1: navigate to the contact / chat if not done yet.
                if not activated and searched:
                    if not search_results_present(fresh, hint.payload):
                        time.sleep(1.2)
                        fresh = executor.observe()
                    if search_results_present(fresh, hint.payload):
                        act = executor.activate_best_result(hint.payload or "", "open")
                        trace.log("navigate_to_chat", ok=act.ok, detail=act.detail)
                        prev_result = f"navigate_to_chat: {'ok' if act.ok else 'FAIL'} - {act.detail}"
                        activated = act.ok
                        if activated:
                            time.sleep(1.2)
                            fresh = executor.observe()

                # Step 2: type and send the message once the chat is open.
                if activated and hint.message:
                    snd = executor.send_message_to_chat(hint.message)
                    trace.log("send_message", ok=snd.ok, detail=snd.detail, message=hint.message)
                    prev_result = f"send_message: {'ok' if snd.ok else 'FAIL'} - {snd.detail}"
                    if snd.ok:
                        time.sleep(0.8)
                        fresh = executor.observe()
                        chk = message_sent(hint, fresh)
                        trace.log("goal_check", ok=chk.ok, detail=chk.detail)
                        if chk.ok:
                            executor.flush_cache(True)
                            trace.result = "success"
                            return True
                        # Accept the send attempt even without confirmed text (common
                        # in end-to-end-encrypted chats where history isn't in UIA tree).
                        executor.flush_cache(True)
                        trace.result = "success"
                        trace.log("goal_check", ok=True, detail="message sent (unverified in UIA)")
                        return True
                    # send failed — fall through to repair
            else:
                # ── Media play/open/select path ───────────────────────────────
                # Feishin (and similar apps) have a two-stage flow:
                #   Stage 1: dropdown shows "Search for X" — clicking navigates to results
                #   Stage 2: results page shows actual track rows — double-clicking plays
                #
                # `activated` only becomes True when we click an actual content row.
                # Clicking a nav suggestion ("Search for X") is an intermediate navigation
                # step: we track it separately and retry for the real content on stage 2.
                _want_activate = not activated
                if activated and fresh is not None:
                    _want_activate = state_signature(fresh) != _last_activation_sig
                if _want_activate and searched:
                    payload = hint.payload or ""
                    then = hint.then or "open"

                    if then == "play":
                        activated, nav_done, fresh, prev_result, results_ready = _media_play_stages(
                            executor, hint, trace, fresh, nav_done=nav_done,
                        )
                        if activated:
                            _last_activation_sig = state_signature(fresh)
                    elif search_results_present(fresh, payload):
                        act = executor.activate_best_result(payload, then)
                        trace.log("activate_result", ok=act.ok, detail=act.detail, then=then)
                        prev_result = f"activate_result: {'ok' if act.ok else 'FAIL'} - {act.detail}"
                        if act.ok:
                            activated = True
                            _last_activation_sig = state_signature(fresh)
                            time.sleep(2.5)
                            fresh = executor.observe()

            done = False
            if activated and fresh is not None and hint.then != "send_message":
                chk = result_activated(hint, fresh)
                for _ in range(10):
                    if chk.ok:
                        break
                    time.sleep(0.5)
                    fresh = executor.observe()
                    chk = result_activated(hint, fresh)
                done = chk.ok
                trace.log("goal_check", ok=done, detail=chk.detail)
            if all_ok and done:
                executor.flush_cache(True)
                trace.result = "success"
                return True
            # Before activation, keep letting the planner make progress toward results.
            # But never loop back for more search once the results page is already open.
            if not activated:
                if nav_done or results_ready:
                    executor.flush_cache(False)
                    repair_budget -= 1
                    trace.log("repair", remaining=repair_budget,
                              previous=prev_result or "results visible but play not verified")
                    if repair_budget < 0:
                        trace.result = "failed"
                        trace.failure_reason = prev_result or "could not play result on search page"
                        return False
                    continue
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
                trace.failure_reason = prev_result or "could not complete search-and-activate"
                return False
            continue

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
