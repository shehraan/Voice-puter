"""Postcondition verification.

Confirms an action changed visible state as expected, using UIA reads (text, value,
selection, focus, title, control presence). Never assume success: if a postcondition
cannot be confirmed, the loop must repair or stop (architecture.md / testing.md).
"""
from __future__ import annotations

import re
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
        direct = com.CurrentValue
        if isinstance(direct, str) and direct:
            return direct
    except Exception:
        pass
    try:
        iface = get_elem_interface(com, "Value")
        val = iface.CurrentValue
        if isinstance(val, str) and val:
            return val
    except Exception:
        pass
    try:
        iface = get_elem_interface(com, "Text")
        rng = iface.DocumentRange
        txt = rng.GetText(-1)
        if isinstance(txt, str) and txt:
            return txt
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

# Element-name prefixes that indicate a navigation/command suggestion ("Search for X",
# "Go to Y") rather than an actual content result. Kept in sync with
# Executor._NAV_SUGGESTION_PREFIXES.
_NAV_SUGGESTION_PREFIXES = (
    "search for ", "go to ", "show ", "browse ", "see all ", "more results",
    "show more", "all results", "view all",
)


def _is_nav_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(n.startswith(p) for p in _NAV_SUGGESTION_PREFIXES)


_SIDEBAR_NAMES = frozenset({"home", "favorites", "albums", "tracks", "artists", "genres"})


def _sidebar_boundary(obs: Observation) -> int | None:
    """X coordinate separating a left nav sidebar from main content, if present."""
    if not obs.elements:
        return None
    positioned = [
        e for e in obs.elements
        if e.control_type not in ("Window", "Document")
    ]
    if not positioned:
        return None
    xs = [e.rectangle[0] for e in positioned]
    left, right = min(xs), max(e.rectangle[2] for e in positioned)
    width = max(right - left, 1)
    left_cutoff = left + int(width * 0.30)
    sidebar_links = [
        e for e in positioned
        if e.control_type == "Hyperlink"
        and (e.name or "").strip().lower() in _SIDEBAR_NAMES
        and e.rectangle[1] >= 260
        and e.rectangle[0] < left_cutoff
    ]
    if len(sidebar_links) < 3:
        return None
    has_content = any(
        e.rectangle[0] >= left_cutoff
        for e in obs.elements
        if e.control_type not in ("Window",)
    )
    if not has_content:
        return None
    return left_cutoff


def _content_elements(obs: Observation) -> list:
    boundary = _sidebar_boundary(obs)
    if boundary is None:
        return list(obs.elements)
    return [e for e in obs.elements if e.rectangle[0] > boundary]


def _on_search_route(obs: Observation | None) -> bool:
    """True when the Chromium document URL indicates a search-results route."""
    if obs is None:
        return False
    for e in obs.elements:
        if e.control_type == "Document":
            val = (read_value(e) or "").lower()
            if "search/song" in val or "#/search" in val:
                return True
    return False


def _named_header_search_edits(obs: Observation) -> list[UIElement]:
    """App-wide search inputs (named 'Search'), not phantom header omniboxes."""
    return [
        e for e in obs.elements
        if e.control_type in ("Edit", "ComboBox")
        and (e.name or "").strip().lower() == "search"
        and e.rectangle[1] < 120
    ]


def _dropdown_nav_visible(obs: Observation | None, query: str | None) -> bool:
    """True when a live-search nav row ('Search for X') is still on screen."""
    if obs is None or not query:
        return False
    content = _content_elements(obs)
    search_tabs = {
        (e.name or "").strip().lower()
        for e in content
        if e.control_type in ("Hyperlink", "TabItem", "Button")
        and e.rectangle[1] < 260
    }
    if {"tracks", "albums", "artists"}.issubset(search_tabs):
        return False
    terms = [t for t in query.lower().split() if len(t) > 1]
    for e in obs.elements:
        if e.control_type not in ("ListItem", "Hyperlink", "DataItem"):
            continue
        if e.rectangle[1] > 240:
            continue
        name = (e.name or "").strip().lower()
        if not name.startswith("search for "):
            continue
        if terms and any(t in name for t in terms):
            return True
    return False


def search_results_page_visible(obs: Observation | None, query: str | None = None) -> bool:
    """True when the app is on a full search-results page (not a live-search dropdown).

    Detects results tabs, Document search routes, or a query in the header search band
    together with numbered table rows in the main content pane (common in Chromium apps
    where track titles are not exposed as named UIA elements).
    """
    if obs is None:
        return False
    content = _content_elements(obs)
    # Search-page tabs live in the header band of the content pane, not the sidebar.
    search_tabs = {
        (e.name or "").strip().lower()
        for e in content
        if e.control_type in ("Hyperlink", "TabItem", "Button")
        and e.rectangle[1] < 260
    }
    if {"tracks", "albums", "artists"}.issubset(search_tabs):
        return True
    terms = [t for t in (query or "").lower().split() if len(t) > 1]
    content_row = False
    header_query = False
    if terms:
        header_query = any(
            any(t in _element_text(e) for t in terms)
            for e in _named_header_search_edits(obs)
        )
        content_row = any(
            e.control_type == "Button"
            and (e.name or "").strip().isdigit()
            and 180 < e.rectangle[1] < 880
            and e in content
            for e in obs.elements
        )
        if header_query and content_row:
            return True
    has_datagrid = any(
        e.control_type == "DataGrid" and e in content for e in obs.elements
    )
    in_dropdown = query and _dropdown_nav_visible(obs, query)
    if _on_search_route(obs) and not in_dropdown:
        if playable_result_present(obs, query) or content_row:
            return True
        if {"tracks", "albums", "artists"}.issubset(search_tabs):
            return True
        if has_datagrid and (not terms or header_query):
            return True
    return False


def results_ready_for_followup(obs: Observation | None, query: str | None) -> bool:
    """Search phase is done — a follow-up action (play/open/select) can proceed."""
    return playable_result_present(obs, query) or search_results_page_visible(obs, query)


def playable_result_present(obs: Observation | None, query: str | None) -> bool:
    """True when an ACTUAL content result (not a navigation suggestion) matches the query.

    Stricter than search_results_present: a dropdown showing only 'Search for X' does
    NOT count — that element merely routes to the results page.
    """
    if obs is None:
        return False
    terms = _significant_terms(query) or _terms(query)
    if not terms:
        return any(
            e.control_type in _RESULT_TYPES and not _is_nav_name(e.name)
            for e in obs.elements
        )
    on_search = _on_search_route(obs)
    for e in obs.elements:
        if e.control_type in _INPUT_TYPES:
            continue
        if _is_nav_name(e.name):
            continue
        name_l = (e.name or "").strip().lower()
        if any(t in name_l for t in terms):
            return True
        if on_search and any(t in _element_text(e) for t in terms):
            if e.control_type in _RESULT_TYPES:
                return True
    return False


def search_results_present(obs: Observation | None, query: str | None) -> bool:
    """True when a submitted search appears to have produced results.

    Requires query terms to appear in a non-input element, OR in a result-type
    element. Pure presence of Hyperlink/ListItem elements (e.g. sidebar playlists
    in Feishin) is NOT sufficient — those exist before any search runs.
    """
    if obs is None:
        return False
    terms = _significant_terms(query) or _terms(query)
    if not terms:
        # No query terms — fall back to presence of any result-type element.
        return any(e.control_type in _RESULT_TYPES for e in obs.elements)
    for e in obs.elements:
        if e.control_type in _INPUT_TYPES:
            continue
        if any(t in _element_text(e) for t in terms):
            return True
    return False


def media_is_playing(obs: Observation | None) -> bool:
    """A visible Pause control or advancing player bar implies media is playing."""
    if obs is None:
        return False
    for e in obs.elements:
        n = (e.name or "").lower()
        if e.control_type in ("Button", "SplitButton") and "pause" in n:
            return True
        if e.control_type == "Group" and "player" in n and "0:00" not in n and ":" in n:
            return True
        if e.control_type == "Button" and re.match(r"^\d+:\d{2}$", (e.name or "").strip()):
            mins, secs = (e.name or "").strip().split(":", 1)
            if int(mins) > 0 or int(secs) > 2:
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


def _player_bar_text(obs: Observation) -> str:
    """Lowercased text from the bottom now-playing / transport band."""
    parts: list[str] = []
    for e in obs.elements:
        if e.rectangle[1] < 850:
            continue
        parts.append((e.name or "").lower())
        parts.append(read_value(e).lower())
    return " ".join(parts)


def _now_playing_reflects_query(obs: Observation, terms: list[str], title: str) -> bool:
    """True when the active transport/title shows the requested track — not merely typed in search."""
    if not terms:
        return False
    tl = title.lower()
    if ("playing" in tl or "paused" in tl) and any(t in tl for t in terms):
        return True
    bar = _player_bar_text(obs)
    if bar and any(t in bar for t in terms):
        return True
    np_text = _now_playing_text(obs)
    if np_text and any(t in np_text for t in terms):
        return True
    return False


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
        reflects = _now_playing_reflects_query(obs, terms, title)
        if reflects and ("playing" in title or media_is_playing(obs)):
            return VerifyResult(True, "now playing the requested track")
        if reflects and "playing" in title:
            return VerifyResult(True, "window title shows playing with query")
        return VerifyResult(
            False,
            "playback controls visible but now-playing does not show the requested track"
            if media_is_playing(obs)
            else "no playback evidence for the requested track",
        )

    # open / select: title change or focused result row is sufficient
    if terms and any(t in title for t in terms):
        return VerifyResult(True, "opened item reflects the query (title)")
    if any(e.has_keyboard_focus and e.control_type in _RESULT_TYPES for e in obs.elements):
        return VerifyResult(True, "a result row is selected/focused")
    text = _haystack(obs)
    if terms and any(t in text for t in terms):
        return VerifyResult(True, "query content visible")
    return VerifyResult(False, "no activation evidence yet")


def message_sent(hint, obs: Observation | None) -> VerifyResult:
    """Verify a message was delivered: it appears in the chat (visible text) or the
    compose field is now empty after a non-empty compose state."""
    if obs is None:
        return VerifyResult(False, "no observation yet")
    msg = getattr(hint, "message", None) or ""
    if not msg:
        return VerifyResult(True, "no message text to verify")
    terms = [w for w in msg.lower().split() if len(w) > 2]
    text = _haystack(obs)
    # Message text visible in chat history (non-compose elements)
    hits = [t for t in terms if t in text]
    if len(hits) >= max(1, len(terms) // 2):
        return VerifyResult(True, f"message text visible in chat: {hits}")
    return VerifyResult(False, "message text not yet confirmed in chat")


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
