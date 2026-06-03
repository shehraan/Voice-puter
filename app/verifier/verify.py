"""Postcondition verification.

Confirms an action changed visible state as expected, using UIA reads (text, value,
selection, focus, title, control presence). Never assume success: if a postcondition
cannot be confirmed, the loop must repair or stop (architecture.md / testing.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pywinauto.uia_defines import get_elem_interface

from app.planner.schema import GoalType
from app.ui.elements import Observation, UIElement


@dataclass
class VerifyResult:
    ok: bool
    detail: str = ""


def read_value(element: UIElement) -> str:
    """Read an element's textual content via Value, then Text pattern."""
    com = getattr(getattr(element, "info", None), "element", None)
    if com is None:
        return ""
    try:
        iface = get_elem_interface(com, "Value")
        val = iface.CurrentValue
        if val:
            return str(val)
    except Exception:
        pass
    try:
        iface = get_elem_interface(com, "Text")
        rng = iface.DocumentRange
        txt = rng.GetText(-1)
        if txt:
            return str(txt)
    except Exception:
        pass
    return ""


def _element_text(el: UIElement) -> str:
    return f"{el.name or ''} {read_value(el)}".strip().lower()


def _as_pc(pc: Any) -> tuple[str, str, dict]:
    if isinstance(pc, dict):
        return pc.get("type", ""), pc.get("description", ""), pc.get("args", {}) or {}
    return getattr(pc, "type", ""), getattr(pc, "description", ""), getattr(pc, "args", {}) or {}


def _contains_terms(args: dict, description: str) -> list[str]:
    terms = args.get("contains_any") or args.get("contains") or []
    if isinstance(terms, str):
        terms = [terms]
    return [str(t).lower() for t in terms if str(t).strip()]


def state_signature(obs: Observation | None) -> tuple:
    """Cheap signature of visible state, used to detect progress between turns."""
    if obs is None:
        return ()
    return (obs.window.title, len(obs.elements), tuple(e.name for e in obs.elements[:20]))


def _haystack(obs: Observation) -> str:
    return " ".join(_element_text(e) for e in obs.elements)


_STOPWORDS = {
    "the", "and", "for", "with", "best", "top", "near", "nearby", "some", "please",
    "of", "in", "on", "my", "me", "a", "an", "to", "result", "results",
}


def _terms(payload: str | None) -> list[str]:
    return [t for t in (payload or "").lower().split() if len(t) > 2]


def _significant_terms(payload: str | None) -> list[str]:
    """Discriminating query terms (stopwords removed, longest first)."""
    terms = [t for t in _terms(payload) if t not in _STOPWORDS and len(t) >= 4]
    return sorted(terms, key=len, reverse=True)


def goal_satisfied(hint, obs: Observation | None, baseline: Observation | None) -> VerifyResult | None:
    """Authoritative, goal-derived completion check owned by the loop.

    Returns None when the goal type has no generic check (the loop then falls back to
    the planner's postconditions). This decouples success from the small model's
    self-reported postconditions, which are often too lenient.
    """
    if obs is None:
        return VerifyResult(False, "no observation yet")
    goal = hint.goal
    title = (obs.window.title or "").lower()
    text = _haystack(obs)

    if goal == GoalType.generic_text_entry and hint.payload:
        target = hint.payload.lower()
        ok = target in text
        return VerifyResult(ok, f"typed text {'visible' if ok else 'not visible'}: {hint.payload!r}")

    if goal == GoalType.generic_search and hint.payload:
        # Require a specific query term to appear in the window TITLE: the title only
        # changes on real navigation/results, so typing into a field is not enough and
        # generic words (e.g. "best") in an unrelated tab title do not count.
        target_terms = (_significant_terms(hint.payload) or _terms(hint.payload))[:2]
        hit = [t for t in target_terms if t in title]
        return VerifyResult(bool(hit), f"results reflect query {hit}" if hit else "no results reflecting the query yet")

    if goal in (GoalType.open_app, GoalType.focus_app):
        return VerifyResult(len(obs.elements) > 0, f"{len(obs.elements)} controls visible in target window")

    if goal == GoalType.generic_build_or_run:
        kws = ["build", "run", "terminal", "output", "compil", "succeed", "error", "task", "debug"]
        hit = [k for k in kws if k in text or k in title]
        return VerifyResult(bool(hit), f"build/run surface visible {hit}" if hit else "no build/run output visible yet")

    return None  # let planner postconditions decide


def verify_postcondition(pc: Any, obs: Observation, baseline: Observation | None = None) -> VerifyResult:
    ptype, description, args = _as_pc(pc)
    terms = _contains_terms(args, description)

    if ptype in ("visible_text_contains", "value_contains", "value_equals"):
        haystack = " ".join(_element_text(e) for e in obs.elements)
        if not terms:
            return VerifyResult(False, "no contains_any terms provided")
        hit = [t for t in terms if t in haystack]
        return VerifyResult(bool(hit), f"matched {hit}" if hit else f"none of {terms} visible")

    if ptype == "title_changed":
        if baseline and obs.window.title and obs.window.title != baseline.window.title:
            return VerifyResult(True, f"title -> {obs.window.title!r}")
        return VerifyResult(False, "window title did not change")

    if ptype in ("results_appeared", "visible_state_changed", "new_document"):
        result_types = {"ListItem", "DataItem", "TreeItem", "Hyperlink"}
        now_results = sum(1 for e in obs.elements if e.control_type in result_types)
        if baseline is not None:
            was_results = sum(1 for e in baseline.elements if e.control_type in result_types)
            if now_results > was_results:
                return VerifyResult(True, f"results {was_results} -> {now_results}")
            if obs.window.title != baseline.window.title:
                return VerifyResult(True, f"title -> {obs.window.title!r}")
            if len(obs.elements) != len(baseline.elements):
                return VerifyResult(True, f"controls {len(baseline.elements)} -> {len(obs.elements)}")
            if terms:
                haystack = " ".join(_element_text(e) for e in obs.elements)
                hit = [t for t in terms if t in haystack]
                if hit:
                    return VerifyResult(True, f"matched {hit}")
            return VerifyResult(False, "no visible state change detected")
        return VerifyResult(now_results > 0, f"{now_results} result-like controls visible")

    if ptype == "selection_changed":
        sel = [e for e in obs.elements if e.has_keyboard_focus]
        return VerifyResult(bool(sel), "a control has keyboard focus" if sel else "no selection/focus change")

    if "exist" in ptype:
        if terms:
            haystack = " ".join(_element_text(e) for e in obs.elements)
            hit = [t for t in terms if t in haystack]
            return VerifyResult(bool(hit), f"matched {hit}" if hit else f"none of {terms} present")
        return VerifyResult(len(obs.elements) > 0, f"{len(obs.elements)} controls present")

    if ptype in ("control_exists", "focus_on"):
        ct = args.get("control_type")
        for e in obs.elements:
            text = _element_text(e)
            type_ok = (ct is None) or (e.control_type == ct)
            name_ok = (not terms) or any(t in text for t in terms)
            if type_ok and name_ok:
                if ptype == "focus_on" and not e.has_keyboard_focus:
                    continue
                return VerifyResult(True, f"found {e.control_type} {e.name!r}")
        return VerifyResult(False, f"no control matched type={ct} terms={terms}")

    # Generic fallback: treat as contains check, else as "any change vs baseline".
    if terms:
        haystack = " ".join(_element_text(e) for e in obs.elements)
        hit = [t for t in terms if t in haystack]
        return VerifyResult(bool(hit), f"matched {hit}" if hit else f"none of {terms} visible")
    if baseline is not None:
        changed = (
            obs.window.title != baseline.window.title or len(obs.elements) != len(baseline.elements)
        )
        return VerifyResult(changed, "state changed" if changed else "no detectable change")
    return VerifyResult(False, f"unrecognized postcondition {ptype!r}; cannot confirm")
