"""Shared test fixtures and helpers.

All tests use mocked observations and the deterministic StubPlanner so they never
need a real desktop, Ollama, or a microphone.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.ui.elements import Observation, UIElement, WindowSummary


def make_element(
    selector_id: str = "obs_0",
    name: str = "Test",
    control_type: str = "Edit",
    automation_id: str = "",
    class_name: str = "",
    supported_patterns: list[str] | None = None,
    is_enabled: bool = True,
    is_offscreen: bool = False,
    has_keyboard_focus: bool = False,
    is_keyboard_focusable: bool = True,
    score: float = 5.0,
) -> UIElement:
    info_mock = MagicMock()
    info_mock.element.CurrentHasKeyboardFocus = has_keyboard_focus
    info_mock.element.CurrentIsKeyboardFocusable = is_keyboard_focusable
    info_mock.element.CurrentIsPassword = False
    return UIElement(
        selector_id=selector_id,
        name=name,
        automation_id=automation_id,
        control_type=control_type,
        class_name=class_name,
        localized_control_type=control_type,
        rectangle=(0, 0, 100, 30),
        is_enabled=is_enabled,
        is_offscreen=is_offscreen,
        has_keyboard_focus=has_keyboard_focus,
        is_keyboard_focusable=is_keyboard_focusable,
        supported_patterns=supported_patterns or ["Value", "LegacyIAccessible"],
        runtime_id=(1, 2, 3),
        depth=2,
        parent_summary="Window",
        sibling_index=0,
        child_count=0,
        score=score,
        info=info_mock,
    )


def make_observation(
    title: str = "Test App",
    process: str = "test.exe",
    elements: list[UIElement] | None = None,
) -> Observation:
    if elements is None:
        elements = [make_element()]
    window = WindowSummary(title=title, process=process, pid=1234, handle=0, is_foreground=True)
    registry = {el.selector_id: el for el in elements}
    return Observation(window=window, elements=elements, registry=registry)


@pytest.fixture
def simple_obs():
    return make_observation()


@pytest.fixture
def search_obs():
    return make_observation(
        title="Test App",
        elements=[
            make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
            make_element("obs_1", "Result 1", "ListItem", supported_patterns=["SelectionItem"]),
            make_element("obs_2", "Result 2", "ListItem", supported_patterns=["SelectionItem"]),
        ],
    )
