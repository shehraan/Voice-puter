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


_RESULT_TYPES = {"ListItem", "DataItem", "TreeItem", "Hyperlink"}
_INPUT_TYPES = {"Edit", "ComboBox", "Document"}


def search_results_present(obs: Observation | None, query: str | None) -> bool:
    """True when a submitted search appears to have produced results.

    Looks for result-like rows or the query terms appearing in a non-input control
    (i.e. somewhere other than the field we typed into).
    """
    if obs is None:
        return False
    if any(e.control_type in _RESULT_TYPES for e in obs.elements):
        return True
    terms = _significant_terms(query) or _terms(query)
    if not terms:
        return False
    for e in obs.elements:
        if e.control_type in _INPUT_TYPES:
            continue
        if any(t in _element_text(e) for t in terms):
            return True
    return False


def media_is_playing(obs: Observation | None) -> bool:
    """A visible Pause control implies media is currently playing."""
    if obs is None:
        return False
    for e in obs.elements:
        n = (e.name or "").lower()
        if e.control_type in ("Button", "SplitButton") and "pause" in n:
            return True
    return False


def _now_playing_text(obs: Observation) -> str:
    """Text from controls that look like a 'now playing' bar/area, lowercased."""
    parts = []
    for e in obs.elements:
        n = (e.name or "").lower()
        # Spotify/media apps surface "Now playing: <track>" or "Group Now playing bar"
        if "now playing" in n or (e.control_type == "Group" and "playing" in n):
            parts.append(n)
    return " ".join(parts)


def result_activated(hint, obs: Observation | None) -> VerifyResult:
    """Confirm a follow-up action (play/open/select) on a search result took effect.

    For 'play': requires genuine playback evidence (pause button visible, or the
    now-playing bar / window title reflects the query). Query terms merely appearing
    in result rows on the search page do NOT count as playback.

    For 'open'/'select': the window title or a selected/focused result suffices.
    """
    if obs is None:
        return VerifyResult(False, "no observation yet")
    query = getattr(hint, "payload", None) or ""
    terms = _significant_terms(query) or _terms(query)
    title = (obs.window.title or "").lower()
    then = getattr(hint, "then", None)

    if then == "play":
        if media_is_playing(obs):
            return VerifyResult(True, "media playing (pause control visible)")
        if terms and any(t in title for t in terms):
            return VerifyResult(True, "now-playing title reflects the query")
        np_text = _now_playing_text(obs)
        if np_text and terms and any(t in np_text for t in terms):
            return VerifyResult(True, "now-playing bar reflects the query")
        return VerifyResult(False, "no playback evidence yet")

    # open / select: title change or focused result row is sufficient
    if terms and any(t in title for t in terms):
        return VerifyResult(True, "opened item reflects the query (title)")
    if any(e.has_keyboard_focus and e.control_type in _RESULT_TYPES for e in obs.elements):
        return VerifyResult(True, "a result row is selected/focused")
    text = _haystack(obs)
    if terms and any(t in text for t in terms):
        return VerifyResult(True, "query content visible")
    return VerifyResult(False, "no activation evidence yet")


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
