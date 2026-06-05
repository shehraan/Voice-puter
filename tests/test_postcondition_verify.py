"""Tests for postcondition verification."""
import pytest

from app.verifier.verify import (
    VerifyResult,
    verify_postcondition,
    search_results_present,
    search_results_page_visible,
    results_ready_for_followup,
)
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


# --- search_results_present ---

def test_search_results_present_query_in_list_item():
    """Results detected when query terms appear in a ListItem."""
    obs = make_observation(elements=[
        make_element("obs_0", "Search", "Edit"),
        make_element("obs_1", "Juicy by Notorious B.I.G.", "ListItem"),
    ])
    assert search_results_present(obs, "juicy")


def test_search_results_present_query_in_button():
    """Results detected when query terms appear in a Button (e.g. 'Play Bohemian Rhapsody')."""
    obs = make_observation(elements=[
        make_element("obs_0", "What do you want to play?", "ComboBox"),
        make_element("obs_1", "Play Bohemian Rhapsody", "Button"),
    ])
    assert search_results_present(obs, "bohemian rhapsody")


def test_search_results_present_feishin_playlists_only_no_query_match():
    """Feishin sidebar playlists (Hyperlinks) without query terms must NOT trigger results-present.

    This was the core bug: 'search_results_present' used to return True the moment
    ANY Hyperlink appeared, causing activate_best_result to pick a sidebar playlist
    instead of an actual search result.
    """
    obs = make_observation(elements=[
        make_element("obs_0", "Search", "Edit"),
        make_element("obs_1", "PLAYLIST", "Hyperlink"),
        make_element("obs_2", "OGMobileUnsmartPlaylist 665", "Hyperlink"),
        make_element("obs_3", "TestPlaylist 1307", "Hyperlink"),
    ])
    # Query "juicy" doesn't appear in any of these elements — must return False.
    assert not search_results_present(obs, "juicy")


def test_search_results_present_feishin_after_successful_search():
    """After a real search, song results matching 'juicy' DO appear alongside playlists."""
    obs = make_observation(elements=[
        make_element("obs_0", "Search", "Edit"),
        make_element("obs_1", "PLAYLIST", "Hyperlink"),
        make_element("obs_2", "OGMobileUnsmartPlaylist 665", "Hyperlink"),
        make_element("obs_3", "Juicy - Notorious B.I.G.", "ListItem"),
        make_element("obs_4", "Juicy (Remix) - Various", "ListItem"),
    ])
    assert search_results_present(obs, "juicy")


def test_search_results_present_no_query_falls_back_to_result_types():
    """When query is empty/None, fall back to any result-type elements present."""
    obs = make_observation(elements=[
        make_element("obs_0", "Search", "Edit"),
        make_element("obs_1", "Some Item", "ListItem"),
    ])
    assert search_results_present(obs, None)
    assert not search_results_present(make_observation(), None)


def test_search_results_page_visible_feishin_tabs():
    """Feishin results page exposes Tracks / Albums / Artists tabs."""
    obs = make_observation(
        title="Search - Feishin",
        elements=[
            make_element("obs_0", "Search", "Edit"),
            make_element("t1", "Tracks", "Hyperlink"),
            make_element("t2", "Albums", "Hyperlink"),
            make_element("t3", "Artists", "Hyperlink"),
            make_element("song", "Narcos", "Hyperlink"),
        ],
    )
    assert search_results_page_visible(obs)


def test_results_ready_for_followup_on_results_page_without_track_in_uia():
    """Results page open is enough to proceed — track row may not match query in UIA yet."""
    obs = make_observation(
        title="Search - Feishin",
        elements=[
            make_element("obs_0", "Search", "Edit"),
            make_element("t1", "Tracks", "Hyperlink"),
            make_element("t2", "Albums", "Hyperlink"),
            make_element("t3", "Artists", "Hyperlink"),
        ],
    )
    assert results_ready_for_followup(obs, "narcos")


def test_search_results_page_visible_via_document_url():
    """Feishin routes the Document URL to #/search/song on the results page."""
    from unittest.mock import patch

    doc = make_element("doc", "Feishin", "Document", supported_patterns=["Value"])
    t1 = make_element("t1", "Tracks", "Hyperlink")
    t2 = make_element("t2", "Albums", "Hyperlink")
    t3 = make_element("t3", "Artists", "Hyperlink")
    obs = make_observation(title="Feishin", elements=[doc, t1, t2, t3])
    with patch(
        "app.verifier.verify.read_value",
        return_value="file:///app/index.html#/search/song?query=narcos",
    ):
        assert search_results_page_visible(obs, "narcos")


def test_search_results_page_not_visible_on_library_table_headers():
    """Library views expose TITLE/ALBUM headers but are not search-results pages."""
    obs = make_observation(
        title="Feishin",
        elements=[
            make_element("obs_0", "TITLE", "Text"),
            make_element("obs_1", "ALBUM", "Text"),
            make_element("obs_2", "GENRE", "Text"),
            make_element("obs_3", "YEAR", "Text"),
        ],
    )
    assert not search_results_page_visible(obs)


def test_search_results_page_not_visible_on_artist_page_counts():
    """Artist/library pages mention albums/tracks in counts but are not search results."""
    obs = make_observation(
        title="Feishin",
        elements=[
            make_element("obs_0", "Search", "Edit"),
            make_element("obs_1", "Albums 0 results", "Hyperlink"),
            make_element("obs_2", "Tracks 0 results", "Hyperlink"),
            make_element("obs_3", "Album Artists 0 results", "Hyperlink"),
        ],
    )
    assert not search_results_page_visible(obs, "narcos")


def test_result_activated_play_rejects_unrelated_paused_track():
    """A Pause button for a different queued track must not count as playing the query."""
    from app.nlp.goal_shortlist import GoalHint
    from app.planner.schema import GoalType
    from app.verifier.verify import result_activated

    hint = GoalHint(goal=GoalType.generic_search, target_app="feishin", payload="narcos", then="play")
    obs = make_observation(
        title="(Paused) Heavy — Linkin Park — Feishin",
        elements=[
            make_element("pause", "Pause", "Button", supported_patterns=["Invoke"]),
            make_element("track", "Heavy", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    chk = result_activated(hint, obs)
    assert not chk.ok
    assert "requested track" in chk.detail


def test_result_activated_play_accepts_matching_now_playing_title():
    from app.nlp.goal_shortlist import GoalHint
    from app.planner.schema import GoalType
    from app.verifier.verify import result_activated

    hint = GoalHint(goal=GoalType.generic_search, target_app="feishin", payload="narcos", then="play")
    obs = make_observation(
        title="(Playing) Narcos — Migos — Feishin",
        elements=[
            make_element("pause", "Pause", "Button", supported_patterns=["Invoke"]),
        ],
    )
    chk = result_activated(hint, obs)
    assert chk.ok
