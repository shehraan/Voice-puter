"""Observed UI element model and the per-observation registry.

An ``Observation`` is the only thing the planner is allowed to ground actions
against. Each element gets a stable ``selector_id`` (stable only within that
observation) and carries the underlying pywinauto element info so the executor
can act on it without re-walking the tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WindowSummary:
    title: str
    process: str
    pid: int
    handle: int
    is_foreground: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "process": self.process,
            "pid": self.pid,
            "handle": hex(self.handle) if self.handle else None,
            "is_foreground": self.is_foreground,
        }


@dataclass
class UIElement:
    selector_id: str
    name: str
    automation_id: str
    control_type: str
    class_name: str
    localized_control_type: str
    rectangle: tuple[int, int, int, int]
    is_enabled: bool
    is_offscreen: bool
    has_keyboard_focus: bool
    is_keyboard_focusable: bool
    supported_patterns: list[str]
    runtime_id: tuple[int, ...]
    depth: int
    parent_summary: str
    sibling_index: int
    child_count: int
    score: float = 0.0
    # Underlying pywinauto UIAElementInfo; never serialized into traces/planner prompt.
    info: Any = field(default=None, repr=False, compare=False)

    def to_compact(self) -> dict[str, Any]:
        """Compact, planner-facing view. No giant trees, no live handles."""
        return {
            "selector_id": self.selector_id,
            "name": self.name,
            "control_type": self.control_type,
            "automation_id": self.automation_id,
            "localized_control_type": self.localized_control_type,
            "is_enabled": self.is_enabled,
            "is_offscreen": self.is_offscreen,
            "has_keyboard_focus": self.has_keyboard_focus,
            "supported_patterns": self.supported_patterns,
            "parent": self.parent_summary,
        }


@dataclass
class Observation:
    window: WindowSummary
    elements: list[UIElement]
    registry: dict[str, UIElement] = field(default_factory=dict)

    def find(self, selector_id: str | None) -> UIElement | None:
        if not selector_id:
            return None
        return self.registry.get(selector_id)

    def to_compact(self) -> dict[str, Any]:
        return {
            "window": self.window.to_dict(),
            "actionable_controls": [e.to_compact() for e in self.elements],
        }
