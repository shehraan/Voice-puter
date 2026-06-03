"""Semantic selector scoring.

Given a semantic role (and optional planner hints), score observed controls so the
executor can ground an abstract request like "the search box" onto a concrete observed
element. Scoring never invents elements; it only ranks what was observed.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.ui.elements import UIElement

# Semantic role -> control-type weights, name keywords, and preferred UIA patterns.
SEMANTIC_ROLES: dict[str, dict] = {
    "search_or_input": {
        "types": {"Edit": 5.0, "ComboBox": 4.0, "Document": 3.0},
        "names": ["search", "find", "query", "address", "url", "ask", "type", "command", "omnibox"],
        "patterns": ["Value"],
    },
    "editable_document": {
        "types": {"Document": 5.0, "Edit": 5.0},
        "names": ["text editor", "document", "editor", "body", "note", "message"],
        "patterns": ["Value", "Text"],
    },
    "result_item": {
        "types": {"ListItem": 5.0, "DataItem": 5.0, "TreeItem": 3.0, "Hyperlink": 3.0, "Text": 2.0},
        "names": ["result", "song", "track", "item", "row", "match"],
        "patterns": ["SelectionItem", "Invoke"],
    },
    "submit_or_primary": {
        "types": {"Button": 5.0, "SplitButton": 4.0, "MenuItem": 3.0},
        "names": ["open", "play", "run", "create", "save", "submit", "send", "ok", "go", "start", "search"],
        "patterns": ["Invoke"],
    },
    "named_control": {
        "types": {"Button": 4.0, "MenuItem": 4.0, "CheckBox": 3.0, "TabItem": 3.0, "Hyperlink": 3.0, "ListItem": 2.0},
        "names": [],
        "patterns": ["Invoke", "SelectionItem", "Toggle", "ExpandCollapse"],
    },
    "menu_or_command": {
        "types": {"MenuItem": 5.0, "Button": 3.0},
        "names": ["file", "edit", "view", "menu", "command", "more", "options", "tools"],
        "patterns": ["Invoke", "ExpandCollapse"],
    },
    "build_or_run": {
        "types": {"Button": 5.0, "MenuItem": 4.0, "SplitButton": 4.0},
        "names": ["build", "run", "start", "debug", "compile", "task", "play", "execute"],
        "patterns": ["Invoke"],
    },
    "field_by_label": {
        "types": {"Edit": 5.0, "ComboBox": 4.0, "Document": 3.0},
        "names": [],
        "patterns": ["Value"],
    },
}

_INPUT_ROLES = {"search_or_input", "editable_document", "field_by_label"}


@dataclass
class ScoredElement:
    element: UIElement
    score: float


def _name_similarity(name: str, keywords: list[str]) -> float:
    if not name or not keywords:
        return 0.0
    low = name.lower()
    best = 0.0
    for kw in keywords:
        if kw in low:
            best = max(best, 1.0)
        else:
            best = max(best, SequenceMatcher(None, low, kw).ratio())
    return best


def score_element(
    element: UIElement,
    role: str,
    name_contains_any: list[str] | None = None,
    preferred_types: list[str] | None = None,
    history_bonus: float = 0.0,
) -> float:
    spec = SEMANTIC_ROLES.get(role, SEMANTIC_ROLES["named_control"])
    score = 0.0

    type_weights = dict(spec["types"])
    for t in preferred_types or []:
        type_weights[t] = max(type_weights.get(t, 0.0), 4.0)
    score += type_weights.get(element.control_type, 0.0)

    role_name_score = _name_similarity(element.name, spec["names"])
    hint_name_score = _name_similarity(element.name, [h.lower() for h in (name_contains_any or [])])
    score += 3.0 * max(role_name_score, hint_name_score)

    role_patterns = set(spec["patterns"])
    if role_patterns & set(element.supported_patterns):
        score += 2.0

    if element.is_enabled:
        score += 0.5
    else:
        score -= 2.0
    if element.is_offscreen:
        score -= 3.0
    if role in _INPUT_ROLES and element.is_keyboard_focusable:
        score += 1.0
    if element.has_keyboard_focus:
        score += 0.5

    score += history_bonus
    return score


def rank_elements(
    elements: list[UIElement],
    role: str,
    name_contains_any: list[str] | None = None,
    preferred_types: list[str] | None = None,
    history_lookup=None,
) -> list[ScoredElement]:
    scored = []
    for el in elements:
        bonus = history_lookup(el) if history_lookup else 0.0
        scored.append(ScoredElement(el, score_element(el, role, name_contains_any, preferred_types, bonus)))
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
