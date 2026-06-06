"""Compact UIA observation engine.

Walks the target window's UIA tree with bounded breadth/depth, scores controls by
actionability, keeps the top N, and probes UIA patterns only for the survivors. The
output is intentionally small so the planner never receives a giant raw tree
(ui-automation-visible.md / performance.md).
"""
from __future__ import annotations

import win32gui
from pywinauto.uia_defines import pattern_ids
from pywinauto.uia_element_info import UIAElementInfo

from app.core.config import LoopConfig
from app.launch.windows import exe_for_pid
from app.ui.elements import Observation, UIElement, WindowSummary

# Patterns worth probing for actionable controls (kept small for speed).
_PROBE_PATTERNS = [
    "Invoke",
    "Value",
    "Toggle",
    "ExpandCollapse",
    "SelectionItem",
    "Selection",
    "Text",
    "Scroll",
    "Window",
    "LegacyIAccessible",
]

# Control types that are usually directly actionable / interesting.
_ACTIONABLE_TYPES = {
    "Edit": 6.0,
    "Document": 5.0,
    "ComboBox": 5.0,
    "Button": 4.5,
    "SplitButton": 4.5,
    "MenuItem": 4.0,
    "Hyperlink": 3.5,
    "ListItem": 3.5,
    "DataItem": 5.0,
    "DataGrid": 6.0,
    "Group": 3.0,
    "TreeItem": 3.0,
    "TabItem": 3.0,
    "CheckBox": 3.0,
    "RadioButton": 3.0,
    "MenuItem ": 4.0,
}

# Names whose substrings strongly suggest a useful semantic control.
_NAME_HINTS = (
    "search",
    "find",
    "query",
    "address",
    "url",
    "open",
    "new",
    "save",
    "run",
    "build",
    "play",
    "submit",
    "send",
    "result",
    "title",
    "date",
    "time",
    "create",
    "add",
    "command",
    "terminal",
    "ask",
)


def _supported_patterns(com_element) -> list[str]:
    found: list[str] = []
    for name in _PROBE_PATTERNS:
        pid = pattern_ids[name][0]
        try:
            if com_element.GetCurrentPattern(pid):
                found.append(name)
        except Exception:
            pass
    return found


def _window_summary(info: UIAElementInfo) -> WindowSummary:
    handle = info.handle or 0
    try:
        fg = win32gui.GetForegroundWindow()
        is_fg = bool(handle) and handle == fg
    except Exception:
        is_fg = False
    pid = info.process_id or 0
    process = exe_for_pid(pid)
    return WindowSummary(
        title=info.name or "",
        process=process,
        pid=pid,
        handle=handle,
        is_foreground=is_fg,
    )


def _score_raw(info: UIAElementInfo, name: str, ctype: str, enabled: bool, offscreen: bool) -> float:
    score = _ACTIONABLE_TYPES.get(ctype, 0.0)
    low = (name or "").lower()
    if low and any(h in low for h in _NAME_HINTS):
        score += 2.5
    if name:
        score += 0.5
    if enabled:
        score += 0.5
    else:
        score -= 1.0
    if offscreen:
        score -= 2.0
    return score


def _root_info(window) -> UIAElementInfo:
    """Accept a WindowSpecification, a UIA wrapper, or a raw UIAElementInfo."""
    if isinstance(window, UIAElementInfo):
        return window
    if hasattr(window, "wrapper_object"):
        return window.wrapper_object().element_info
    if hasattr(window, "element_info"):
        return window.element_info
    return window


def observe_window(window, cfg: LoopConfig | None = None) -> Observation:
    """Observe a single top-level window and return a compact, scored observation."""
    cfg = cfg or LoopConfig()
    root_info = _root_info(window)
    summary = _window_summary(root_info)

    # Bounded BFS over element infos (cheap: no wrapper construction during walk).
    candidates: list[tuple[float, UIAElementInfo, int, str, int]] = []
    visited = 0
    max_visited = 2000
    queue: list[tuple[UIAElementInfo, int, str, int]] = [(root_info, 0, summary.title or "window", 0)]
    while queue and visited < max_visited:
        info, depth, parent_name, sib_index = queue.pop(0)
        visited += 1
        try:
            children = info.children()
        except Exception:
            children = []
        if depth < cfg.observe_max_depth:
            label = (info.name or info.control_type or "node")[:40]
            for i, child in enumerate(children):
                queue.append((child, depth + 1, label, i))
        if depth == 0:
            continue  # skip the window root itself as an actionable control
        try:
            name = info.name or ""
            ctype = info.control_type or ""
            enabled = bool(info.enabled)
            offscreen = not bool(info.visible)
        except Exception:
            continue
        score = _score_raw(info, name, ctype, enabled, offscreen)
        if score <= 0:
            continue
        candidates.append((score, info, depth, parent_name, sib_index))

    candidates.sort(key=lambda c: c[0], reverse=True)
    kept = candidates[: cfg.observe_max_controls]

    elements: list[UIElement] = []
    registry: dict[str, UIElement] = {}
    idx = 0
    for score, info, depth, parent_name, sib_index in kept:
        # Elements can go stale between the walk and the detailed read (especially in
        # Electron/Chromium UIs that re-render). Skip any element that throws.
        try:
            com = info.element
            try:
                localized = com.CurrentLocalizedControlType
            except Exception:
                localized = ""
            try:
                has_focus = bool(com.CurrentHasKeyboardFocus)
                focusable = bool(com.CurrentIsKeyboardFocusable)
            except Exception:
                has_focus = focusable = False
            try:
                r = info.rectangle
                rect = (r.left, r.top, r.right, r.bottom)
            except Exception:
                rect = (0, 0, 0, 0)
            try:
                rid = tuple(info.runtime_id or ())
            except Exception:
                rid = ()
            el = UIElement(
                selector_id=f"obs_{idx}",
                name=info.name or "",
                automation_id=info.automation_id or "",
                control_type=info.control_type or "",
                class_name=info.class_name or "",
                localized_control_type=localized or "",
                rectangle=rect,
                is_enabled=bool(info.enabled),
                is_offscreen=not bool(info.visible),
                has_keyboard_focus=has_focus,
                is_keyboard_focusable=focusable,
                supported_patterns=_supported_patterns(com),
                runtime_id=rid,
                depth=depth,
                parent_summary=parent_name,
                sibling_index=sib_index,
                child_count=0,
                score=round(score, 2),
                info=info,
            )
        except Exception:
            continue
        elements.append(el)
        registry[el.selector_id] = el
        idx += 1

    return Observation(window=summary, elements=elements, registry=registry)
