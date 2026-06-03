"""Tests for selector repair: stale selectors are rediscovered, not blindly retried."""
import pytest

from app.cache.selector_cache import SelectorCache, SelectorDescriptor
from tests.conftest import make_element, make_observation


@pytest.fixture
def cache(tmp_path):
    return SelectorCache(path=tmp_path / "cache.json")


def test_degraded_descriptor_not_returned(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="SearchBox")
    cache.record_success("app", "search_or_input", el)
    cache.record_failure("app", "search_or_input")
    cache.record_failure("app", "search_or_input")

    # Degraded: get() must return None so the executor re-discovers
    assert cache.get("app", "search_or_input") is None


def test_match_in_observation_prefers_automation_id(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="SearchBox")
    cache.record_success("app", "search_or_input", el)
    desc = cache.get("app", "search_or_input")

    # Observation has same automation_id but different name
    obs = make_observation(elements=[
        make_element("obs_0", "Find", "Edit", automation_id="SearchBox"),
        make_element("obs_1", "OK", "Button"),
    ])
    matched = cache.match_in_observation(desc, obs)
    assert matched is not None
    assert matched.automation_id == "SearchBox"


def test_match_in_observation_falls_back_to_name_regex(cache):
    el = make_element("obs_0", "Search", "Edit", automation_id="")
    cache.record_success("app", "search_or_input", el)
    desc = cache.get("app", "search_or_input")

    obs = make_observation(elements=[
        make_element("obs_0", "Search", "Edit", automation_id=""),
        make_element("obs_1", "Play", "Button"),
    ])
    matched = cache.match_in_observation(desc, obs)
    assert matched is not None
    assert matched.name == "Search"


def test_repair_records_failure_then_rediscovers(cache):
    """Simulated repair cycle: record failure, then record success on new element."""
    el_old = make_element("obs_0", "Old Search", "Edit", automation_id="OldAID")
    cache.record_success("app", "search_or_input", el_old)
    cache.record_failure("app", "search_or_input")
    cache.record_failure("app", "search_or_input")
    # degraded
    assert cache.get("app", "search_or_input") is None

    # Rediscovery: record success with new element (restores entry)
    el_new = make_element("obs_0", "New Search", "Edit", automation_id="NewAID")
    cache.record_success("app", "search_or_input", el_new)

    desc = cache.get("app", "search_or_input")
    assert desc is not None
    assert desc.automation_id == "NewAID"
    assert not desc.degraded
