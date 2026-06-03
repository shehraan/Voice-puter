"""Tests for the selector cache: record, revalidate, degrade, repair."""
import json
import tempfile
from pathlib import Path

import pytest

from app.cache.selector_cache import SelectorCache
from tests.conftest import make_element, make_observation


@pytest.fixture
def cache(tmp_path):
    return SelectorCache(path=tmp_path / "selector_cache.json")


def test_record_success_creates_entry(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="SearchBox")
    cache.record_success("notepad", "search_or_input", el)
    desc = cache.get("notepad", "search_or_input")
    assert desc is not None
    assert desc.control_type == "Edit"
    assert desc.success_count == 1
    assert not desc.degraded


def test_record_failure_increments(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="SearchBox")
    cache.record_success("notepad", "search_or_input", el)
    cache.record_failure("notepad", "search_or_input")
    desc = cache.get("notepad", "search_or_input")
    assert desc.failure_count == 1


def test_degrades_after_two_failures(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="SearchBox")
    cache.record_success("notepad", "search_or_input", el)
    cache.record_failure("notepad", "search_or_input")
    cache.record_failure("notepad", "search_or_input")
    # degraded: get() should return None
    assert cache.get("notepad", "search_or_input") is None


def test_success_clears_failure_count(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="SearchBox")
    cache.record_success("notepad", "search_or_input", el)
    cache.record_failure("notepad", "search_or_input")
    cache.record_success("notepad", "search_or_input", el)
    desc = cache.get("notepad", "search_or_input")
    assert desc.failure_count == 0
    assert not desc.degraded


def test_match_in_observation_by_automation_id(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="SearchBox")
    cache.record_success("notepad", "search_or_input", el)
    desc = cache.get("notepad", "search_or_input")

    # New observation with same automation_id
    obs = make_observation(elements=[
        make_element("obs_0", "Search", "Edit", automation_id="SearchBox"),
        make_element("obs_1", "OK", "Button"),
    ])
    matched = cache.match_in_observation(desc, obs)
    assert matched is not None
    assert matched.automation_id == "SearchBox"


def test_match_in_observation_no_match(cache):
    el = make_element("obs_0", "SearchBar", "Edit", automation_id="SearchBar")
    cache.record_success("app", "search_or_input", el)
    desc = cache.get("app", "search_or_input")

    obs = make_observation(elements=[make_element("obs_0", "Play", "Button")])
    matched = cache.match_in_observation(desc, obs)
    assert matched is None


def test_persists_to_json(tmp_path):
    p = tmp_path / "cache.json"
    c1 = SelectorCache(path=p)
    el = make_element("obs_0", "X", "Edit", automation_id="AID")
    c1.record_success("app", "role", el)

    c2 = SelectorCache(path=p)
    desc = c2.get("app", "role")
    assert desc is not None
    assert desc.automation_id == "AID"
