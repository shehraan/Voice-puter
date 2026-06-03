"""Tests for postcondition verification."""
import pytest

from app.verifier.verify import VerifyResult, verify_postcondition
from tests.conftest import make_element, make_observation


def _pc(ptype, **kwargs):
    return {"type": ptype, "description": "", "args": kwargs}


# --- visible_text_contains ---

def test_text_contains_pass():
    obs = make_observation(elements=[make_element("obs_0", "hello world")])
    result = verify_postcondition(_pc("visible_text_contains", contains_any=["hello world"]), obs)
    assert result.ok


def test_text_contains_fail():
    obs = make_observation(elements=[make_element("obs_0", "some other text")])
    result = verify_postcondition(_pc("visible_text_contains", contains_any=["hello world"]), obs)
    assert not result.ok


def test_text_contains_no_terms_fails():
    obs = make_observation()
    result = verify_postcondition(_pc("visible_text_contains"), obs)
    assert not result.ok


# --- title_changed ---

def test_title_changed_pass():
    baseline = make_observation(title="Old Title")
    current = make_observation(title="New Title - Results")
    result = verify_postcondition(_pc("title_changed"), current, baseline)
    assert result.ok


def test_title_changed_fail():
    baseline = make_observation(title="Same Title")
    current = make_observation(title="Same Title")
    result = verify_postcondition(_pc("title_changed"), current, baseline)
    assert not result.ok


# --- results_appeared ---

def test_results_appeared_more_list_items():
    from app.ui.elements import UIElement
    baseline = make_observation(elements=[make_element("obs_0", "Search", "Edit")])
    result_elements = [
        make_element("obs_0", "Search", "Edit"),
        make_element("obs_1", "Result 1", "ListItem"),
        make_element("obs_2", "Result 2", "ListItem"),
    ]
    current = make_observation(elements=result_elements)
    result = verify_postcondition(_pc("results_appeared"), current, baseline)
    assert result.ok


def test_results_appeared_no_change_fail():
    obs = make_observation()
    result = verify_postcondition(_pc("results_appeared"), obs, obs)
    assert not result.ok


# --- selection_changed ---

def test_selection_changed_with_focused_element():
    obs = make_observation(elements=[
        make_element("obs_0", "Item 1", "ListItem", has_keyboard_focus=True),
    ])
    result = verify_postcondition(_pc("selection_changed"), obs)
    assert result.ok


def test_selection_changed_no_focus_fail():
    obs = make_observation(elements=[
        make_element("obs_0", "Item 1", "ListItem", has_keyboard_focus=False),
    ])
    result = verify_postcondition(_pc("selection_changed"), obs)
    assert not result.ok


# --- control_exists ---

def test_control_exists_found():
    obs = make_observation(elements=[make_element("obs_0", "Run", "Button")])
    result = verify_postcondition(_pc("control_exists", control_type="Button", contains_any=["run"]), obs)
    assert result.ok


def test_control_exists_not_found():
    obs = make_observation(elements=[make_element("obs_0", "Save", "Button")])
    result = verify_postcondition(_pc("control_exists", control_type="Button", contains_any=["build"]), obs)
    assert not result.ok


# --- unknown postcondition types ---

def test_unknown_pc_type_fails_without_baseline():
    obs = make_observation()
    result = verify_postcondition(_pc("made_up_condition"), obs)
    assert not result.ok
