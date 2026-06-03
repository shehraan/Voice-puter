"""Tests for semantic selector scoring."""
import pytest

from app.ui.selectors import rank_elements, score_element
from tests.conftest import make_element


def test_search_field_scores_highest_for_search_role():
    search = make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"])
    button = make_element("obs_1", "OK", "Button", supported_patterns=["Invoke"])
    ranked = rank_elements([search, button], role="search_or_input")
    assert ranked[0].element.selector_id == "obs_0"


def test_disabled_element_penalized():
    enabled = make_element("obs_0", "Search", "Edit", is_enabled=True)
    disabled = make_element("obs_1", "Search box", "Edit", is_enabled=False)
    ranked = rank_elements([enabled, disabled], role="search_or_input")
    assert ranked[0].element.selector_id == "obs_0"


def test_offscreen_element_penalized():
    visible = make_element("obs_0", "Search", "Edit", is_offscreen=False)
    offscreen = make_element("obs_1", "Search", "Edit", is_offscreen=True)
    ranked = rank_elements([visible, offscreen], role="search_or_input")
    assert ranked[0].element.selector_id == "obs_0"


def test_name_hint_boosts_score():
    no_hint = make_element("obs_0", "TextField", "Edit")
    with_hint = make_element("obs_1", "Find something", "Edit")
    ranked = rank_elements([no_hint, with_hint], role="search_or_input")
    assert ranked[0].element.selector_id == "obs_1"


def test_history_bonus_applied():
    elem = make_element("obs_0", "OK", "Button", supported_patterns=["Invoke"])
    s_no_bonus = score_element(elem, "submit_or_primary", history_bonus=0.0)
    s_with_bonus = score_element(elem, "submit_or_primary", history_bonus=3.0)
    assert s_with_bonus > s_no_bonus


def test_result_item_matched_by_list_item():
    item = make_element("obs_0", "Track 1", "ListItem", supported_patterns=["SelectionItem"])
    button = make_element("obs_1", "Play", "Button", supported_patterns=["Invoke"])
    ranked = rank_elements([item, button], role="result_item")
    assert ranked[0].element.selector_id == "obs_0"


def test_empty_elements_returns_empty():
    result = rank_elements([], role="search_or_input")
    assert result == []
